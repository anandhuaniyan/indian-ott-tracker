"""Configurable delivery with persistent fingerprint/cooldown deduplication."""
from datetime import datetime, timedelta, timezone
import hashlib, httpx, logging, smtplib
from email.message import EmailMessage
from sqlalchemy.orm import Session
from app.config.settings import settings
from app.models.operations import NotificationLog

class NotificationService:
    def __init__(self, db: Session): self.db = db
    def notify(self, message: str, severity="warning", fingerprint=None, cooldown_minutes=360):
        fingerprint = fingerprint or hashlib.sha256(f"{severity}:{message}".encode()).hexdigest()
        old = self.db.query(NotificationLog).filter_by(fingerprint=fingerprint).filter(NotificationLog.last_notified_at.is_not(None)).order_by(NotificationLog.last_notified_at.desc()).first()
        now = datetime.now(timezone.utc)
        if old and old.last_notified_at:
            last_notified = old.last_notified_at if old.last_notified_at.tzinfo else old.last_notified_at.replace(tzinfo=timezone.utc)
            if last_notified > now - timedelta(minutes=cooldown_minutes): return False
        deliveries = []
        for channel, send in (("discord", self._discord), ("telegram", self._telegram), ("email", self._email)):
            try:
                sent = bool(send(message)); deliveries.append((channel, sent))
            except Exception:
                logging.getLogger(__name__).exception("Notification channel failed: %s", channel)
                deliveries.append((channel, False))
        for channel, sent in deliveries:
            self.db.add(NotificationLog(fingerprint=fingerprint, channel=channel, severity=severity, message=message, last_notified_at=now if sent else None))
        self.db.commit(); return any(sent for _, sent in deliveries)
    def _discord(self, message):
        if not settings.DISCORD_WEBHOOK_URL: return False
        httpx.post(settings.DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10).raise_for_status(); return True
    def _telegram(self, message):
        if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID): return False
        httpx.post(f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": message}, timeout=10).raise_for_status(); return True
    def _email(self, message):
        if not (settings.SMTP_HOST and settings.SMTP_FROM and settings.ADMIN_NOTIFICATION_EMAIL): return False
        email = EmailMessage(); email["Subject"] = "Indian OTT Tracker notification"; email["From"] = settings.SMTP_FROM; email["To"] = settings.ADMIN_NOTIFICATION_EMAIL; email.set_content(message)
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as client:
            client.starttls()
            if settings.SMTP_USERNAME: client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            client.send_message(email)
        return True
