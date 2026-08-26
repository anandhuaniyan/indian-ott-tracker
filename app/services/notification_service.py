"""Configurable delivery with persistent fingerprint/cooldown deduplication."""
from datetime import datetime, timedelta, timezone
import hashlib, httpx
from sqlalchemy.orm import Session
from app.config.settings import settings
from app.models.operations import NotificationLog

class NotificationService:
    def __init__(self, db: Session): self.db = db
    def notify(self, message: str, severity="warning", fingerprint=None, cooldown_minutes=360):
        fingerprint = fingerprint or hashlib.sha256(f"{severity}:{message}".encode()).hexdigest()
        old = self.db.query(NotificationLog).filter_by(fingerprint=fingerprint).order_by(NotificationLog.last_notified_at.desc()).first()
        now = datetime.now(timezone.utc)
        if old and old.last_notified_at and old.last_notified_at > now - timedelta(minutes=cooldown_minutes): return False
        channels = []
        if settings.DISCORD_WEBHOOK_URL:
            httpx.post(settings.DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10).raise_for_status(); channels.append("discord")
        if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
            httpx.post(f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": message}, timeout=10).raise_for_status(); channels.append("telegram")
        for channel in channels: self.db.add(NotificationLog(fingerprint=fingerprint, channel=channel, severity=severity, message=message, last_notified_at=now))
        self.db.commit(); return bool(channels)
