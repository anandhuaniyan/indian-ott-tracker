"""Pinterest admin API — pin management, OAuth, and publishing controls."""

from datetime import datetime, timedelta, timezone
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.config.settings import settings
from app.core.session_auth import require_admin_session, require_same_origin
from app.database.connection import get_db

from app.models.movie import Movie
from app.models.pinterest import PinterestBoard, PinterestPin, PinterestOAuthToken
from app.services.pinterest.client import PinterestClient, PinterestAPIError
from app.services.pinterest.oauth_state import create_state, consume_state
from app.services.pinterest.publishing import (
    build_movie_dict,
    create_pin_candidate,
    determine_pin_type,
    determine_template,
    generate_branded_only_pin,
    generate_destination_url,
    generate_pin,
    generate_pin_description,
    generate_pin_title,
    publish_pin,
)
from app.services.pinterest.pin_generator import TEMPLATES

router = APIRouter(
    prefix="/api/v1/pinterest",
    tags=["Pinterest"],
    dependencies=[Depends(require_same_origin)],
)
callback_router = APIRouter(tags=["Pinterest"])


# ── Schemas ────────────────────────────────────────────────────────────

class PinCreateRequest(BaseModel):
    movie_id: int
    force_template: str | None = Field(default=None, max_length=32)


class PinActionRequest(BaseModel):
    action: str = Field(pattern="^(approve|reject|publish|retry|regenerate)$")


class BoardAssignRequest(BaseModel):
    board_id: int


class OAuthCallbackRequest(BaseModel):
    code: str = Field(min_length=5, max_length=500)
    state: str = Field(min_length=20, max_length=200)


# ── OAuth endpoints ────────────────────────────────────────────────────

# NOTE: Production redirect URI configured as:
# https://otttracker.in/api/v1/integrations/pinterest/callback

@router.get("/auth/url")
def get_auth_url(admin_session_id: str = Depends(require_admin_session)):
    """Get the Pinterest OAuth authorization URL."""
    if not settings.PINTEREST_APP_ID:
        raise HTTPException(
            status_code=400,
            detail="Pinterest app is not configured.",
        )
    client = PinterestClient()
    url = client.get_authorization_url(state=create_state(admin_session_id))
    return {"url": url}


def _exchange_and_store(code: str, db: Session) -> dict:
    if not settings.PINTEREST_APP_ID or not settings.PINTEREST_APP_SECRET:
        raise HTTPException(status_code=400, detail="Pinterest integration is not configured.")

    client = PinterestClient()
    try:
        token_data = client.exchange_code(code)
    except PinterestAPIError as exc:
        raise HTTPException(status_code=400, detail="Pinterest OAuth exchange failed") from exc

    db.query(PinterestOAuthToken).filter_by(is_active=True).update({"is_active": False})
    expires_in = token_data.get("access_token_expiration", 0)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None
    token = PinterestOAuthToken(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        token_type=token_data.get("token_type", "bearer"),
        expires_at=expires_at,
        scope=token_data.get("scope"),
        pinterest_user_id=token_data.get("user_id"),
        is_active=True,
    )
    db.add(token)
    db.commit()
    return {
        "status": "connected",
        "message": "Pinterest account connected successfully",
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


def _validate_oauth_state(state: str, expected_session_id: str | None = None) -> None:
    state_session_id = consume_state(state)
    if state_session_id is None or (
        expected_session_id is not None
        and not secrets.compare_digest(state_session_id, expected_session_id)
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")


@router.post("/auth/callback")
@router.post("/auth/exchange")
def oauth_callback(
    body: OAuthCallbackRequest,
    db: Session = Depends(get_db),
    admin_session_id: str = Depends(require_admin_session),
):
    """Validate single-use state and exchange an authorization code."""
    _validate_oauth_state(body.state, admin_session_id)
    return _exchange_and_store(body.code, db)


@callback_router.get(
    "/api/v1/integrations/pinterest/callback",
    include_in_schema=False,
)
def pinterest_oauth_public_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    """Consume OAuth state and exchange server-side, then return a clean URL."""
    if error:
        return RedirectResponse(url="/admin/pinterest?oauth=denied", status_code=303)
    if not code or not state:
        return RedirectResponse(url="/admin/pinterest?oauth=invalid", status_code=303)
    try:
        _validate_oauth_state(state)
        _exchange_and_store(code, db)
    except HTTPException:
        return RedirectResponse(url="/admin/pinterest?oauth=failed", status_code=303)
    return RedirectResponse(url="/admin/pinterest?oauth=connected", status_code=303)

@router.get("/auth/status")
def auth_status(
    db: Session = Depends(get_db),
    _=Depends(require_admin_session),
):
    """Check if Pinterest OAuth is connected."""
    token = db.query(PinterestOAuthToken).filter_by(is_active=True).first()
    return {
        "connected": token is not None,
        "expires_at": token.expires_at.isoformat() if token else None,
        "user_id": token.pinterest_user_id if token else None,
    }


# ── Board management ───────────────────────────────────────────────────

@router.get("/boards")
def list_boards(
    db: Session = Depends(get_db),
    _=Depends(require_admin_session),
):
    """List Pinterest boards (syncs from API if connected)."""
    boards = db.query(PinterestBoard).filter_by(is_active=True).all()
    return {
        "boards": [
            {
                "id": b.id,
                "pinterest_board_id": b.pinterest_board_id,
                "name": b.name,
                "description": b.description,
                "language_code": b.language_code,
                "url": b.url,
                "pin_count": db.query(func.count(PinterestPin.id)).filter_by(board_id=b.id).scalar() or 0,
            }
            for b in boards
        ]
    }


@router.post("/boards/sync")
def sync_boards(
    db: Session = Depends(get_db),
    _=Depends(require_admin_session),
):
    """Sync boards from Pinterest API."""
    token = db.query(PinterestOAuthToken).filter_by(is_active=True).first()
    if not token:
        raise HTTPException(status_code=400, detail="Not connected to Pinterest")

    client = PinterestClient(access_token=token.access_token)
    try:
        result = client.list_boards(page_size=100)
    except PinterestAPIError as exc:
        raise HTTPException(status_code=400, detail=f"Failed to fetch boards: {exc.detail}")

    synced = 0
    for item in result.get("items", []):
        board_id = item.get("id")
        if not board_id:
            continue
        existing = db.query(PinterestBoard).filter_by(pinterest_board_id=board_id).first()
        if existing:
            existing.name = item.get("name", existing.name)
            existing.description = item.get("description", existing.description)
            existing.url = item.get("url", existing.url)
            existing.last_synced_at = datetime.now(timezone.utc)
        else:
            board = PinterestBoard(
                pinterest_board_id=board_id,
                name=item.get("name", "Untitled"),
                description=item.get("description"),
                url=item.get("url"),
                last_synced_at=datetime.now(timezone.utc),
            )
            db.add(board)
        synced += 1

    db.commit()
    return {"synced": synced}


# ── Pin management ─────────────────────────────────────────────────────

@router.get("/pins")
def list_pins(
    status: str = Query(default=""),
    pin_type: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    _=Depends(require_admin_session),
):
    """List Pinterest pins with filters."""
    query = db.query(PinterestPin).options(
        selectinload(PinterestPin.movie),
        selectinload(PinterestPin.board),
    )

    if status:
        query = query.filter(PinterestPin.status == status)
    if pin_type:
        query = query.filter(PinterestPin.pin_type == pin_type)

    total = query.count()
    pins = (
        query
        .order_by(PinterestPin.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": p.id,
                "movie_id": p.movie_id,
                "movie_title": p.movie.title if p.movie else None,
                "pin_type": p.pin_type,
                "template": p.template,
                "pin_title": p.pin_title,
                "pin_description": p.pin_description,
                "destination_url": p.destination_url,
                "image_path": p.image_path,
                "artwork_source": p.artwork_source,
                "artwork_rights_status": p.artwork_rights_status,
                "pinterest_pin_id": p.pinterest_pin_id,
                "pinterest_pin_url": p.pinterest_pin_url,
                "platform": p.platform,
                "board_id": p.board_id,
                "board_name": p.board.name if p.board else None,
                "status": p.status,
                "generated_at": p.generated_at.isoformat() if p.generated_at else None,
                "published_at": p.published_at.isoformat() if p.published_at else None,
                "last_error": p.last_error,
                "retry_count": p.retry_count,
            }
            for p in pins
        ],
    }


@router.get("/pins/{pin_id}")
def get_pin(
    pin_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin_session),
):
    """Get a single pin detail."""
    pin = (
        db.query(PinterestPin)
        .options(
            selectinload(PinterestPin.movie),
            selectinload(PinterestPin.board),
        )
        .filter_by(id=pin_id)
        .first()
    )
    if not pin:
        raise HTTPException(status_code=404, detail="Pin not found")
    return {
        "id": pin.id,
        "movie_id": pin.movie_id,
        "movie_title": pin.movie.title if pin.movie else None,
        "pin_type": pin.pin_type,
        "template": pin.template,
        "pin_title": pin.pin_title,
        "pin_description": pin.pin_description,
        "destination_url": pin.destination_url,
        "image_path": pin.image_path,
        "artwork_source": pin.artwork_source,
        "artwork_rights_status": pin.artwork_rights_status,
        "pinterest_pin_id": pin.pinterest_pin_id,
        "pinterest_pin_url": pin.pinterest_pin_url,
        "platform": pin.platform,
        "board_id": pin.board_id,
        "board_name": pin.board.name if pin.board else None,
        "status": pin.status,
        "generated_at": pin.generated_at.isoformat() if pin.generated_at else None,
        "published_at": pin.published_at.isoformat() if pin.published_at else None,
        "last_error": pin.last_error,
        "retry_count": pin.retry_count,
    }


@router.post("/pins")
def create_pin(
    body: PinCreateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin_session),
):
    """Create a new pin candidate for a movie."""
    movie = db.query(Movie).filter_by(id=body.movie_id).first()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    pin = create_pin_candidate(movie, db, force_template=body.force_template)
    if pin is None:
        raise HTTPException(status_code=409, detail="Pin already exists or movie ineligible")

    return {
        "id": pin.id,
        "status": pin.status,
        "pin_type": pin.pin_type,
        "template": pin.template,
        "image_path": pin.image_path,
    }


@router.post("/pins/{pin_id}/action")
def pin_action(
    pin_id: int,
    body: PinActionRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin_session),
):
    """Take action on a pin (approve, reject, publish, retry, regenerate)."""
    pin = db.query(PinterestPin).filter_by(id=pin_id).first()
    if not pin:
        raise HTTPException(status_code=404, detail="Pin not found")

    if body.action == "approve":
        if pin.status not in ("READY_FOR_REVIEW", "RIGHTS_REVIEW"):
            raise HTTPException(status_code=400, detail=f"Cannot approve pin in status {pin.status}")
        pin.status = "APPROVED"
        db.commit()
        return {"status": "APPROVED"}

    elif body.action == "reject":
        pin.status = "SKIPPED"
        db.commit()
        return {"status": "SKIPPED"}

    elif body.action == "publish":
        if pin.status not in ("APPROVED", "FAILED"):
            raise HTTPException(status_code=400, detail=f"Cannot publish pin in status {pin.status}")
        if not pin.board_id:
            raise HTTPException(status_code=400, detail="No board assigned")
        success = publish_pin(pin, db)
        return {
            "status": pin.status,
            "pinterest_pin_id": pin.pinterest_pin_id,
            "pinterest_pin_url": pin.pinterest_pin_url,
            "last_error": pin.last_error,
        }

    elif body.action == "retry":
        if pin.status != "FAILED":
            raise HTTPException(status_code=400, detail="Can only retry failed pins")
        success = publish_pin(pin, db)
        return {
            "status": pin.status,
            "pinterest_pin_id": pin.pinterest_pin_id,
            "pinterest_pin_url": pin.pinterest_pin_url,
            "last_error": pin.last_error,
        }

    elif body.action == "regenerate":
        movie = db.query(Movie).filter_by(id=pin.movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")

        # Delete old image if exists
        if pin.image_path:
            from pathlib import Path
            old_path = Path(settings.MEDIA_ROOT).parent / pin.image_path.lstrip("/")
            if old_path.exists():
                old_path.unlink(missing_ok=True)

        # Regenerate
        movie_dict = build_movie_dict(movie, db)
        try:
            filepath, rel_path = generate_branded_only_pin(
                movie=movie_dict,
                template_name=pin.template,
            )
            pin.image_path = rel_path
            pin.artwork_source = "branded_only"
            pin.artwork_rights_status = "BRANDED_ONLY"
            pin.status = "READY_FOR_REVIEW"
            pin.generated_at = datetime.now(timezone.utc)
            pin.last_error = None
            db.commit()
        except Exception as exc:
            pin.status = "FAILED"
            pin.last_error = str(exc)[:500]
            db.commit()
            raise HTTPException(status_code=500, detail=f"Regeneration failed: {exc}")

        return {"status": pin.status, "image_path": pin.image_path}

    raise HTTPException(status_code=400, detail="Unknown action")


@router.post("/pins/{pin_id}/assign-board")
def assign_board(
    pin_id: int,
    body: BoardAssignRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin_session),
):
    """Assign a board to a pin."""
    pin = db.query(PinterestPin).filter_by(id=pin_id).first()
    if not pin:
        raise HTTPException(status_code=404, detail="Pin not found")

    board = db.query(PinterestBoard).filter_by(id=body.board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    pin.board_id = board.id
    db.commit()
    return {"board_id": board.id, "board_name": board.name}


# ── Stats ──────────────────────────────────────────────────────────────

@router.get("/stats")
def pin_stats(
    db: Session = Depends(get_db),
    _=Depends(require_admin_session),
):
    """Get Pinterest publishing statistics."""
    status_counts = dict(
        db.query(PinterestPin.status, func.count(PinterestPin.id))
        .group_by(PinterestPin.status)
        .all()
    )
    type_counts = dict(
        db.query(PinterestPin.pin_type, func.count(PinterestPin.id))
        .group_by(PinterestPin.pin_type)
        .all()
    )
    total = db.query(func.count(PinterestPin.id)).scalar() or 0
    published = db.query(func.count(PinterestPin.id)).filter_by(status="PUBLISHED").scalar() or 0

    return {
        "total_pins": total,
        "published": published,
        "by_status": status_counts,
        "by_type": type_counts,
        "pinterest_connected": db.query(PinterestOAuthToken).filter_by(is_active=True).first() is not None,
        "boards_count": db.query(func.count(PinterestBoard.id)).filter_by(is_active=True).scalar() or 0,
    }


# ── Templates ──────────────────────────────────────────────────────────

@router.get("/templates")
def list_templates(_=Depends(require_admin_session)):
    """List available pin templates."""
    return {
        "templates": [
            {"name": name, "badge": config["badge"], "accent": config["accent"]}
            for name, config in TEMPLATES.items()
        ]
    }


# ── Bulk generation ────────────────────────────────────────────────────

class BulkGenerateRequest(BaseModel):
    movie_ids: list[int] = Field(min_length=1, max_length=50)
    force_template: str | None = None


@router.post("/bulk-generate")
def bulk_generate(
    body: BulkGenerateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin_session),
):
    """Generate pins for multiple movies at once."""
    results = []
    for movie_id in body.movie_ids:
        movie = db.query(Movie).filter_by(id=movie_id).first()
        if not movie:
            results.append({"movie_id": movie_id, "status": "error", "message": "Movie not found"})
            continue
        pin = create_pin_candidate(movie, db, force_template=body.force_template)
        if pin:
            results.append({
                "movie_id": movie_id,
                "pin_id": pin.id,
                "status": pin.status,
                "pin_type": pin.pin_type,
            })
        else:
            results.append({"movie_id": movie_id, "status": "skipped", "message": "Duplicate or ineligible"})
    return {"results": results}
