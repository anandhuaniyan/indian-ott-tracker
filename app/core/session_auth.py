"""Cookie-based administrator authentication; API keys remain automation-only."""
import hashlib, hmac, secrets
from fastapi import Cookie, HTTPException, Request, status
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from app.config.settings import settings

COOKIE = "ott_admin_session"
def _signer(): return URLSafeTimedSerializer(settings.ADMIN_SESSION_SECRET or settings.SECRET_KEY, salt="admin-session")
def verify_password(value: str) -> bool:
    # ADMIN_PASSWORD_HASH format: pbkdf2_sha256$iterations$salt_hex$digest_hex
    try:
        scheme, rounds, salt, digest = settings.ADMIN_PASSWORD_HASH.split("$")
        if scheme != "pbkdf2_sha256": return False
        actual = hashlib.pbkdf2_hmac("sha256", value.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, digest)
    except ValueError: return False
def create_session(): return _signer().dumps({"admin": True, "nonce": secrets.token_urlsafe(16)})
def require_admin_session(ott_admin_session: str | None = Cookie(default=None)):
    if not ott_admin_session: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    try:
        if not _signer().loads(ott_admin_session, max_age=60*60*8).get("admin"): raise ValueError
    except (BadSignature, SignatureExpired, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")


def require_same_origin(request: Request):
    """Reject cross-origin cookie-authenticated mutations while allowing non-browser clients."""
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed = {item.strip().rstrip("/") for item in settings.FRONTEND_ORIGINS.split(",") if item.strip()}
    allowed.add(settings.SITE_URL.rstrip("/"))
    if origin.rstrip("/") not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-origin admin request rejected")
