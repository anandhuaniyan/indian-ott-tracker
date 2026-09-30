"""Shared, escaped OTT Tracker transactional-email layout and SMTP delivery."""

from email.message import EmailMessage
from html import escape
import smtplib
from urllib.parse import urlparse

from app.config.settings import settings


def absolute_site_url(path: str = "") -> str:
    base = settings.SITE_URL.rstrip("/")
    return f"{base}/{path.lstrip('/')}" if path else base


def canonical_poster_url(value: str | None) -> str | None:
    """Resolve only known OTT Tracker/TMDB artwork locations."""
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith(("/media/", "/storage/", "/branding/")):
        return absolute_site_url(value)
    if value.startswith("/"):
        return f"https://image.tmdb.org/t/p/w500{value}"
    parsed = urlparse(value)
    site_host = urlparse(settings.SITE_URL).hostname
    if parsed.scheme == "https" and parsed.hostname in {"image.tmdb.org", site_host}:
        return value
    return None


def build_transactional_email(
    *,
    to: str,
    subject: str,
    heading: str,
    intro: str,
    plain: str,
    details: list[tuple[str, object | None]] | None = None,
    action_label: str | None = None,
    action_url: str | None = None,
    poster_url: str | None = None,
) -> EmailMessage:
    safe_details = details or []
    detail_html = "".join(
        f'<tr><td style="padding:5px 12px 5px 0;color:#697386;vertical-align:top">{escape(label)}</td>'
        f'<td style="padding:5px 0;color:#172033;font-weight:600">{escape(str(value))}</td></tr>'
        for label, value in safe_details
        if value not in (None, "")
    )
    action_html = ""
    if action_label and action_url:
        action_html = (
            f'<p style="margin:24px 0"><a href="{escape(action_url, quote=True)}" '
            'style="display:inline-block;padding:12px 18px;border-radius:8px;background:#d99a22;color:#17130b;'
            f'font-weight:700;text-decoration:none">{escape(action_label)}</a></p>'
            f'<p style="font-size:12px;color:#697386;overflow-wrap:anywhere">If the button does not work, copy this URL: '
            f'{escape(action_url)}</p>'
        )
    poster_html = (
        f'<img src="{escape(poster_url, quote=True)}" alt="" width="120" '
        'style="display:block;max-width:120px;height:auto;border-radius:10px;margin:0 0 18px" />'
        if poster_url
        else ""
    )
    html = (
        '<!doctype html><html><body style="margin:0;background:#f3f5f8;color:#172033">'
        '<div style="display:none;max-height:0;overflow:hidden">OTT Tracker account notification</div>'
        '<div style="max-width:620px;margin:0 auto;padding:24px 12px;font-family:Arial,sans-serif;line-height:1.55">'
        '<div style="padding:18px 22px;background:#151820;color:#fff;border-radius:12px 12px 0 0">'
        '<strong style="font-size:20px">OTT <span style="color:#ffbf47">Tracker</span></strong></div>'
        '<div style="padding:24px 22px;background:#fff;border:1px solid #dfe3ea;border-top:0;border-radius:0 0 12px 12px">'
        f'{poster_html}<h1 style="margin:0 0 12px;font-size:24px">{escape(heading)}</h1>'
        f'<p style="margin:0 0 16px">{escape(intro)}</p>'
        f'{"<table role=\"presentation\" style=\"border-collapse:collapse;margin:16px 0\">" + detail_html + "</table>" if detail_html else ""}'
        f'{action_html}<p style="margin:26px 0 0;color:#697386;font-size:12px">This transactional message was sent by OTT Tracker. '
        'If you did not request it, you can ignore it.</p></div></div></body></html>'
    )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM
    message["To"] = to
    message.set_content(plain)
    message.add_alternative(html, subtype="html")
    return message


def smtp_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_FROM)


def deliver_email(message: EmailMessage) -> None:
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as client:
        if settings.SMTP_USE_TLS:
            client.starttls()
        if settings.SMTP_USERNAME:
            client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        client.send_message(message)
