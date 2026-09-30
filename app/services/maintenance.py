"""Shared maintenance-window state and safe public serialization."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.operations import MaintenanceWindow


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def effective_status(item: MaintenanceWindow, now: datetime | None = None) -> str:
    now = as_utc(now or datetime.now(timezone.utc))
    if item.status == "SCHEDULED" and as_utc(item.start_time) <= now < as_utc(item.end_time):
        return "ACTIVE"
    return item.status


def serialize_maintenance(item: MaintenanceWindow, now: datetime | None = None) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "start_time": as_utc(item.start_time).isoformat(),
        "end_time": as_utc(item.end_time).isoformat(),
        "status": effective_status(item, now),
        "stored_status": item.status,
        "affected_services": list(item.affected_services or []),
        "impact_level": item.impact_level,
        "impact_message": item.impact_message,
        "created_at": as_utc(item.created_at).isoformat(),
        "updated_at": as_utc(item.updated_at).isoformat(),
        "completed_at": as_utc(item.completed_at).isoformat() if item.completed_at else None,
        "cancelled_at": as_utc(item.cancelled_at).isoformat() if item.cancelled_at else None,
    }


def public_maintenance_snapshot(db: Session, now: datetime | None = None) -> dict:
    now = as_utc(now or datetime.now(timezone.utc))
    rows = db.query(MaintenanceWindow).all()
    active = sorted(
        (
            item for item in rows
            if item.status == "SCHEDULED"
            and as_utc(item.start_time) <= now < as_utc(item.end_time)
        ),
        key=lambda item: as_utc(item.start_time),
    )
    upcoming = sorted(
        (
            item for item in rows
            if item.status == "SCHEDULED" and as_utc(item.start_time) > now
        ),
        key=lambda item: as_utc(item.start_time),
    )
    history = sorted(
        (item for item in rows if item.status in {"COMPLETED", "CANCELLED"}),
        key=lambda item: as_utc(item.start_time),
        reverse=True,
    )[:20]
    return {
        "overall_status": "ACTIVE" if active else "SCHEDULED" if upcoming else "OPERATIONAL",
        "active": [serialize_maintenance(item, now) for item in active],
        "upcoming": [serialize_maintenance(item, now) for item in upcoming],
        "history": [serialize_maintenance(item, now) for item in history],
        "updated_at": now.isoformat(),
    }
