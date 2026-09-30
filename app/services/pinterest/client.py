"""Pinterest API client for OAuth and pin management."""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)

PINTEREST_API_BASE = "https://api.pinterest.com/v5"
PINTEREST_AUTH_BASE = "https://www.pinterest.com/oauth"


class PinterestAPIError(Exception):
    """Raised when Pinterest API returns an error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Pinterest API {status_code}: {detail}")


class PinterestClient:
    """Low-level Pinterest API v5 client."""

    def __init__(self, access_token: str | None = None) -> None:
        self.access_token = access_token or settings.PINTEREST_ACCESS_TOKEN

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{PINTEREST_API_BASE}{path}"
        try:
            response = httpx.request(
                method,
                url,
                headers=self._headers(),
                timeout=30.0,
                **kwargs,
            )
            if response.status_code >= 400:
                detail = response.text[:500]
                logger.error("Pinterest API %s %s -> %s: %s", method, path, response.status_code, detail)
                raise PinterestAPIError(response.status_code, detail)
            return response.json()
        except httpx.HTTPError as exc:
            logger.error("Pinterest HTTP error: %s", exc)
            raise

    # ── OAuth ──────────────────────────────────────────────────────────

    def get_authorization_url(self, state: str | None = None) -> str:
        """Build the Pinterest OAuth authorization URL."""
        params = {
            "client_id": settings.PINTEREST_APP_ID,
            "redirect_uri": settings.PINTEREST_REDIRECT_URI,
            "response_type": "code",
            "scope": "boards:read,boards:write,pins:read,pins:write",
        }
        if state:
            params["state"] = state
        return f"{PINTEREST_AUTH_BASE}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        """Exchange an authorization code for access/refresh tokens."""
        credentials = base64.b64encode(
            f"{settings.PINTEREST_APP_ID}:{settings.PINTEREST_APP_SECRET}".encode()
        ).decode()
        response = httpx.post(
            f"{PINTEREST_API_BASE}/oauth/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.PINTEREST_REDIRECT_URI,
            },
            timeout=30.0,
        )
        if response.status_code >= 400:
            raise PinterestAPIError(response.status_code, response.text[:500])
        return response.json()

    def refresh_access_token(self, refresh_token: str) -> dict:
        """Refresh an expired access token."""
        credentials = base64.b64encode(
            f"{settings.PINTEREST_APP_ID}:{settings.PINTEREST_APP_SECRET}".encode()
        ).decode()
        response = httpx.post(
            f"{PINTEREST_API_BASE}/oauth/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=30.0,
        )
        if response.status_code >= 400:
            raise PinterestAPIError(response.status_code, response.text[:500])
        return response.json()

    # ── Boards ─────────────────────────────────────────────────────────

    def list_boards(self, page_size: int = 25, bookmark: str | None = None) -> dict:
        """List boards for the authenticated user."""
        params: dict = {"page_size": page_size}
        if bookmark:
            params["bookmark"] = bookmark
        return self._request("GET", "/boards", params=params)

    def create_board(self, name: str, description: str | None = None) -> dict:
        """Create a new board."""
        body: dict = {"name": name}
        if description:
            body["description"] = description
        return self._request("POST", "/boards", json=body)

    # ── Pins ───────────────────────────────────────────────────────────

    def create_pin(
        self,
        board_id: str,
        title: str,
        description: str,
        link: str,
        image_url: str | None = None,
        image_base64: str | None = None,
        alt_text: str | None = None,
    ) -> dict:
        """Create a pin on a board."""
        body: dict = {
            "board_id": board_id,
            "title": title[:100],
            "description": description[:800],
            "link": link[:2048],
        }
        if image_url:
            body["media_source"] = {
                "source_type": "image_url",
                "url": image_url,
            }
        elif image_base64:
            body["media_source"] = {
                "source_type": "image_base64",
                "data": image_base64,
                "content_type": "image/png",
            }
        if alt_text:
            body["alt_text"] = alt_text[:500]
        return self._request("POST", "/pins", json=body)

    def get_pin(self, pin_id: str) -> dict:
        """Get pin details."""
        return self._request("GET", f"/pins/{pin_id}")

    def delete_pin(self, pin_id: str) -> dict:
        """Delete a pin."""
        return self._request("DELETE", f"/pins/{pin_id}")

    def get_user_boards(self) -> dict:
        """Get the authenticated user's boards."""
        return self._request("GET", "/user_account/boards")
