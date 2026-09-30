"""Fail-safe Cloudflare Worker route control for the maintenance Worker."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import secrets
from typing import Iterator

import httpx
from redis import Redis

from app.config.settings import settings
from app.core.secrets import sanitize_error


logger = logging.getLogger("otttracker.maintenance.audit")
LOCK_KEY = "otttracker:cloudflare-maintenance-route-change"


class MaintenanceControlError(RuntimeError):
    """A public-safe maintenance control failure."""


class MaintenanceBusyError(MaintenanceControlError):
    """Another route change is already in progress."""


@dataclass(frozen=True)
class Route:
    id: str
    pattern: str
    script: str | None


class CloudflareMaintenanceService:
    def __init__(self, client: httpx.Client | None = None, redis_client: Redis | None = None):
        self.client = client
        self.redis = redis_client

    @property
    def target_patterns(self) -> tuple[str, ...]:
        values = tuple(dict.fromkeys(item.strip() for item in settings.CLOUDFLARE_MAINTENANCE_ROUTES.split(",") if item.strip()))
        if not values:
            raise MaintenanceControlError("Cloudflare maintenance routes are not configured")
        return values

    def _configuration_error(self) -> str | None:
        missing = [name for name in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ZONE_ID") if not getattr(settings, name).strip()]
        return f"Missing configuration: {', '.join(missing)}" if missing else None

    @contextmanager
    def _http(self) -> Iterator[httpx.Client]:
        if self.client:
            yield self.client
            return
        with httpx.Client(
            base_url=settings.CLOUDFLARE_API_BASE_URL.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}", "Content-Type": "application/json"},
            timeout=settings.CLOUDFLARE_ROUTE_TIMEOUT_SECONDS,
        ) as client:
            yield client

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            with self._http() as client:
                response = client.request(method, path, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MaintenanceControlError(sanitize_error(exc, limit=500)) from exc
        if not isinstance(payload, dict) or payload.get("success") is not True:
            errors = payload.get("errors") if isinstance(payload, dict) else None
            raise MaintenanceControlError(sanitize_error(errors or "Cloudflare returned an invalid response", limit=500))
        return payload

    def list_routes(self) -> list[Route]:
        config_error = self._configuration_error()
        if config_error:
            raise MaintenanceControlError(config_error)
        payload = self._request("GET", f"/zones/{settings.CLOUDFLARE_ZONE_ID}/workers/routes")
        if not isinstance(payload.get("result"), list):
            raise MaintenanceControlError("Cloudflare route response did not contain a route list")
        routes = []
        for item in payload["result"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("pattern"), str):
                raise MaintenanceControlError("Cloudflare route response contained an invalid route")
            routes.append(Route(id=item["id"], pattern=item["pattern"], script=item.get("script")))
        return routes

    def status(self) -> dict:
        routes = self.list_routes()
        targets = {route.pattern: route for route in routes if route.pattern in self.target_patterns}
        worker = settings.CLOUDFLARE_MAINTENANCE_WORKER
        active = sorted(pattern for pattern, route in targets.items() if route.script == worker)
        conflicts = sorted(pattern for pattern, route in targets.items() if route.script != worker)
        if len(active) == len(self.target_patterns):
            state = "ON"
        elif not active:
            state = "OFF" if not conflicts else "CONFLICT"
        else:
            state = "PARTIAL"
        return {"state": state, "worker": worker, "target_routes": list(self.target_patterns), "active_routes": active, "conflicting_routes": conflicts}

    def _redis(self) -> Redis:
        return self.redis or Redis.from_url(settings.REDIS_URL, decode_responses=True)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        client = self._redis()
        token = secrets.token_urlsafe(24)
        try:
            acquired = client.set(LOCK_KEY, token, nx=True, ex=settings.CLOUDFLARE_MAINTENANCE_LOCK_SECONDS)
        except Exception as exc:
            raise MaintenanceControlError("Maintenance lock store is unavailable") from exc
        if not acquired:
            raise MaintenanceBusyError("Another maintenance route change is in progress")
        try:
            yield
        finally:
            try:
                client.eval("if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end", 1, LOCK_KEY, token)
            except Exception:
                logger.error("maintenance_lock_release_failed")

    def change(self, action: str, actor: str) -> dict:
        action = action.lower()
        if action not in {"on", "off"}:
            raise MaintenanceControlError("Unsupported maintenance action")
        previous = None
        try:
            with self._lock():
                previous = self.status()
                if action == "on":
                    self._enable(previous)
                    expected = "ON"
                else:
                    self._disable()
                    expected = "OFF"
                result = self.status()
                if result["state"] != expected:
                    raise MaintenanceControlError(f"Cloudflare verification failed: expected {expected}, got {result['state']}")
            self._audit(actor, action, previous, result, True)
            return result
        except Exception as exc:
            self._audit(actor, action, previous, {"error": sanitize_error(exc, limit=500)}, False)
            if isinstance(exc, MaintenanceControlError):
                raise
            raise MaintenanceControlError("Maintenance route change failed") from exc

    def _enable(self, previous: dict) -> None:
        if previous["conflicting_routes"]:
            raise MaintenanceControlError("A target route is assigned to another Worker; no routes were changed")
        existing = set(previous["active_routes"])
        for pattern in self.target_patterns:
            if pattern not in existing:
                self._request("POST", f"/zones/{settings.CLOUDFLARE_ZONE_ID}/workers/routes", json={"pattern": pattern, "script": settings.CLOUDFLARE_MAINTENANCE_WORKER})

    def _disable(self) -> None:
        worker = settings.CLOUDFLARE_MAINTENANCE_WORKER
        for route in self.list_routes():
            if route.pattern in self.target_patterns and route.script == worker:
                self._request("DELETE", f"/zones/{settings.CLOUDFLARE_ZONE_ID}/workers/routes/{route.id}")

    @staticmethod
    def _audit(actor: str, action: str, previous: dict | None, result: dict, success: bool) -> None:
        logger.info(json.dumps({
            "event": "cloudflare_maintenance_route_change",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": actor,
            "action": action,
            "previous_state": previous,
            "result": result,
            "success": success,
        }, sort_keys=True))
