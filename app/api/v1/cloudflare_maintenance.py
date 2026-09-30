"""Private adapter used by the existing Discord bot for /maintenance."""

import hmac

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.config.settings import settings
from app.services.cloudflare_maintenance import CloudflareMaintenanceService, MaintenanceBusyError, MaintenanceControlError


router = APIRouter(prefix="/api/v1/maintenance/discord", tags=["Maintenance Control"])


class MaintenanceCommand(BaseModel):
    interaction_id: str = Field(min_length=2, max_length=120)
    actor_id: str = Field(min_length=1, max_length=100)
    guild_id: str | None = Field(default=None, max_length=100)
    action: str = Field(pattern="^(on|off|status)$")


def _authorized(secret: str | None, payload: MaintenanceCommand) -> bool:
    expected = settings.DISCORD_MAINTENANCE_SHARED_SECRET
    if not expected or not secret or not hmac.compare_digest(secret, expected):
        return False
    admins = {item.strip() for item in settings.DISCORD_MAINTENANCE_ADMIN_IDS.split(",") if item.strip()}
    if not admins or payload.actor_id not in admins:
        return False
    guild = settings.DISCORD_MAINTENANCE_GUILD_ID.strip()
    return not guild or payload.guild_id == guild


@router.post("")
def maintenance_command(payload: MaintenanceCommand, x_discord_maintenance_secret: str | None = Header(default=None)):
    if not _authorized(x_discord_maintenance_secret, payload):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Discord maintenance administrator authorization required")
    service = CloudflareMaintenanceService()
    try:
        result = service.status() if payload.action == "status" else service.change(payload.action, f"discord:{payload.actor_id}")
    except MaintenanceBusyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except MaintenanceControlError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return {"status": "OK", "interaction_id": payload.interaction_id, **result}
