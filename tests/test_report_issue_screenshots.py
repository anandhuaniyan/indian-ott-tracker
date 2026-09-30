from io import BytesIO
import hashlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config.settings import settings
from app.database.connection import get_db
from app.main import app
from app.models.operations import ContactRequest
from app.services.contact_requests import ContactRequestNotificationService
from app.services.report_issue_uploads import stored_screenshot_path


@pytest.fixture()
def client(database):
    def override():
        yield database

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def image_bytes(image_format: str) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (24, 16), "#293b80").save(stream, format=image_format)
    return stream.getvalue()


def form_data(comment: str = "The page layout is misaligned.") -> dict[str, str]:
    return {
        "request_type": "WEBSITE_ISSUE",
        "name": "Screenshot Reporter",
        "email": "reporter@example.test",
        "comment": comment,
    }


def configure_isolated_notifications(monkeypatch):
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(settings, "DISCORD_BOT_ENDPOINT", "")
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", "")
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "SMTP_FROM", "")
    monkeypatch.setattr(settings, "ADMIN_NOTIFICATION_EMAIL", "")


@pytest.mark.parametrize(
    ("extension", "mime_type", "image_format"),
    [("png", "image/png", "PNG"), ("jpg", "image/jpeg", "JPEG"), ("webp", "image/webp", "WEBP")],
)
def test_report_issue_accepts_valid_screenshot_formats(
    client, database, monkeypatch, tmp_path, extension, mime_type, image_format
):
    configure_isolated_notifications(monkeypatch)
    monkeypatch.setattr(settings, "REPORT_ISSUE_UPLOAD_ROOT", str(tmp_path))
    response = client.post(
        "/api/v1/contact-requests",
        data=form_data(f"Valid {extension} screenshot."),
        files={"screenshot": (f"screen.{extension}", image_bytes(image_format), mime_type)},
    )
    assert response.status_code == 201, response.text
    assert response.json()["screenshot_attached"] is True
    item = database.query(ContactRequest).filter_by(request_id=response.json()["request_id"]).one()
    assert item.screenshot_mime_type == mime_type
    assert item.screenshot_size > 0
    assert item.screenshot_path.startswith("issue_") and item.screenshot_path.endswith(f".{extension}")
    assert stored_screenshot_path(item.screenshot_path).is_file()


def test_report_issue_without_screenshot_keeps_json_contract(client, database, monkeypatch):
    configure_isolated_notifications(monkeypatch)
    response = client.post("/api/v1/contact-requests", json=form_data())
    assert response.status_code == 201
    assert response.json()["screenshot_attached"] is False
    item = database.query(ContactRequest).filter_by(request_id=response.json()["request_id"]).one()
    assert item.screenshot_path is None


@pytest.mark.parametrize(
    ("case", "filename", "mime_type", "status", "message"),
    [
        ("type", "notes.txt", "text/plain", 415, "Please upload a PNG, JPG, or WEBP image."),
        ("malformed", "fake.png", "image/png", 422, "The selected file is not a valid image."),
        ("mismatch", "fake.jpg", "image/jpeg", 422, "The image content does not match its file type."),
        ("size", "large.png", "image/png", 413, "Screenshot must be 5 MB or smaller."),
    ],
    ids=("invalid-type", "malformed-image", "mime-mismatch", "over-5mb"),
)
def test_report_issue_rejects_unsafe_screenshots(
    client, database, monkeypatch, tmp_path, case, filename, mime_type, status, message
):
    configure_isolated_notifications(monkeypatch)
    monkeypatch.setattr(settings, "REPORT_ISSUE_UPLOAD_ROOT", str(tmp_path))
    content = (
        b"x" * (5 * 1024 * 1024 + 1)
        if case == "size"
        else image_bytes("PNG") if case == "mismatch" else b"not an image"
    )
    response = client.post(
        "/api/v1/contact-requests",
        data=form_data(),
        files={"screenshot": (filename, content, mime_type)},
    )
    assert response.status_code == status
    assert response.json()["detail"] == message
    assert database.query(ContactRequest).count() == 0
    assert not list(tmp_path.glob("*"))


def test_screenshot_names_are_unique_and_admin_can_view_image(
    client, database, monkeypatch, tmp_path
):
    configure_isolated_notifications(monkeypatch)
    monkeypatch.setattr(settings, "REPORT_ISSUE_UPLOAD_ROOT", str(tmp_path))
    ids = []
    for index in range(2):
        response = client.post(
            "/api/v1/contact-requests",
            data=form_data(f"Screenshot number {index} has an issue."),
            files={"screenshot": ("../../same-name.png", image_bytes("PNG"), "image/png")},
        )
        assert response.status_code == 201
        ids.append(response.json()["request_id"])
    rows = database.query(ContactRequest).order_by(ContactRequest.id).all()
    assert rows[0].screenshot_path != rows[1].screenshot_path
    assert all(".." not in row.screenshot_path and "/" not in row.screenshot_path for row in rows)

    salt = b"0123456789abcdef"
    password = "screenshot-admin"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}")
    assert client.post("/api/v1/admin/login", json={"password": password}).status_code == 200
    detail = client.get(f"/api/v1/admin/contact-requests/{ids[0]}")
    assert detail.status_code == 200
    assert detail.json()["screenshot_url"].endswith(f"/{ids[0]}/screenshot")
    image = client.get(detail.json()["screenshot_url"])
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    assert image.content == image_bytes("PNG")


def test_contact_email_attaches_stored_screenshot(database, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "REPORT_ISSUE_UPLOAD_ROOT", str(tmp_path))
    filename = "issue_test.png"
    (tmp_path / filename).write_bytes(image_bytes("PNG"))
    item = ContactRequest(
        request_id="WEB-SCREENSHOT",
        request_type="WEBSITE_ISSUE",
        email="reporter@example.test",
        comment="Screenshot attached.",
        screenshot_path=filename,
        screenshot_mime_type="image/png",
        screenshot_size=(tmp_path / filename).stat().st_size,
    )
    database.add(item)
    database.commit()
    message = ContactRequestNotificationService(database)._message(item, "RECEIVED")
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "image/png"
    assert attachments[0].get_filename() == "report_screenshot.png"
    admin_message = ContactRequestNotificationService(database)._admin_message(item)
    admin_attachments = list(admin_message.iter_attachments())
    assert len(admin_attachments) == 1
    assert admin_attachments[0].get_content_type() == "image/png"
