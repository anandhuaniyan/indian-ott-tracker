"""Validation and private storage for report-issue screenshots."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
import tempfile
import uuid
import warnings

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.config.settings import settings


MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024
ALLOWED_SCREENSHOT_TYPES = {
    "image/png": ("PNG", ".png"),
    "image/jpeg": ("JPEG", ".jpg"),
    "image/webp": ("WEBP", ".webp"),
}


class ScreenshotUploadError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ValidatedScreenshot:
    data: bytes
    mime_type: str
    extension: str


def upload_root() -> Path:
    return Path(settings.REPORT_ISSUE_UPLOAD_ROOT).resolve()


def stored_screenshot_path(filename: str) -> Path:
    # Only server-generated basenames are accepted, even if the DB is tampered with.
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise ValueError("Invalid stored screenshot path")
    root = upload_root()
    candidate = (root / safe_name).resolve()
    if candidate.parent != root:
        raise ValueError("Invalid stored screenshot path")
    return candidate


async def validate_screenshot(upload: UploadFile) -> ValidatedScreenshot:
    claimed_type = (upload.content_type or "").lower()
    if claimed_type not in ALLOWED_SCREENSHOT_TYPES:
        raise ScreenshotUploadError("Please upload a PNG, JPG, or WEBP image.", 415)

    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await upload.read(min(1024 * 1024, MAX_SCREENSHOT_BYTES + 1 - size))
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_SCREENSHOT_BYTES:
            raise ScreenshotUploadError("Screenshot must be 5 MB or smaller.", 413)
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise ScreenshotUploadError("The selected screenshot is empty.")

    expected_format, extension = ALLOWED_SCREENSHOT_TYPES[claimed_type]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                actual_format = (image.format or "").upper()
                image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise ScreenshotUploadError("The selected file is not a valid image.") from None
    if actual_format != expected_format:
        raise ScreenshotUploadError("The image content does not match its file type.")
    return ValidatedScreenshot(data=data, mime_type=claimed_type, extension=extension)


def store_screenshot(screenshot: ValidatedScreenshot) -> tuple[str, int]:
    root = upload_root()
    root.mkdir(parents=True, exist_ok=True)
    filename = f"issue_{uuid.uuid4().hex}{screenshot.extension}"
    destination = stored_screenshot_path(filename)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".upload_", delete=False) as handle:
            temporary_name = handle.name
            handle.write(screenshot.data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        # The API writes the private volume while the unprivileged worker reads
        # attachments from it. No execute bit is granted and it is never served
        # by the public static-file mount.
        destination.chmod(0o644)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
    return filename, len(screenshot.data)


def delete_screenshot(filename: str | None) -> None:
    if not filename:
        return
    try:
        stored_screenshot_path(filename).unlink(missing_ok=True)
    except ValueError:
        pass
