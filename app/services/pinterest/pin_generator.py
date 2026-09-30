"""OTTTracker-branded Pinterest pin image generator.

Creates 1000x1500 vertical Pinterest images with OTTTracker branding,
movie metadata, and safe layout of existing artwork (never AI-recreated).
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config.settings import settings

logger = logging.getLogger(__name__)

# ── Dimensions ─────────────────────────────────────────────────────────
PIN_WIDTH = 1000
PIN_HEIGHT = 1500

# ── Font paths (Windows) ──────────────────────────────────────────────
FONT_DIR = Path("C:/Windows/Fonts")
FONT_REGULAR = FONT_DIR / "segoeui.ttf"
FONT_BOLD = FONT_DIR / "segoeuib.ttf"
FONT_LIGHT = FONT_DIR / "segoeuil.ttf"

# ── Color palette ──────────────────────────────────────────────────────
COLORS = {
    "bg_dark": (15, 15, 25),
    "bg_gradient_top": (20, 15, 35),
    "bg_gradient_bottom": (10, 10, 20),
    "accent_blue": (0, 150, 255),
    "accent_green": (0, 200, 120),
    "accent_purple": (140, 80, 255),
    "accent_orange": (255, 140, 0),
    "accent_red": (255, 80, 80),
    "accent_teal": (0, 180, 180),
    "text_white": (255, 255, 255),
    "text_light": (220, 220, 230),
    "text_muted": (160, 160, 175),
    "text_dim": (100, 100, 115),
    "card_bg": (25, 25, 40),
    "card_border": (50, 50, 70),
    "overlay_dark": (0, 0, 0, 180),
    "overlay_light": (0, 0, 0, 100),
    "brand_gradient_start": (0, 120, 255),
    "brand_gradient_end": (100, 60, 255),
}

# ── Template configs ───────────────────────────────────────────────────
TEMPLATES = {
    "NOW_STREAMING": {
        "accent": "accent_green",
        "badge": "NOW STREAMING",
        "badge_bg": (0, 160, 80),
    },
    "OTT_RELEASE": {
        "accent": "accent_blue",
        "badge": "OTT RELEASE",
        "badge_bg": (0, 120, 220),
    },
    "COMING_SOON": {
        "accent": "accent_orange",
        "badge": "COMING SOON",
        "badge_bg": (220, 120, 0),
    },
    "MOVIE_DISCOVERY": {
        "accent": "accent_purple",
        "badge": "MOVIE DISCOVERY",
        "badge_bg": (120, 60, 220),
    },
    "HIGHLY_RATED": {
        "accent": "accent_teal",
        "badge": "HIGHLY RATED",
        "badge_bg": (0, 150, 150),
    },
    "THIS_WEEK_ON_OTT": {
        "accent": "accent_red",
        "badge": "THIS WEEK ON OTT",
        "badge_bg": (200, 60, 60),
    },
}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REGULAR
    try:
        return ImageFont.truetype(str(path), size)
    except (OSError, IOError):
        return ImageFont.load_default()


def _gradient_overlay(
    width: int, height: int, color_top: tuple, color_bottom: tuple
) -> Image.Image:
    """Create a vertical gradient overlay."""
    gradient = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(gradient)
    for y in range(height):
        ratio = y / height
        r = int(color_top[0] + (color_bottom[0] - color_top[0]) * ratio)
        g = int(color_top[1] + (color_bottom[1] - color_top[1]) * ratio)
        b = int(color_top[2] + (color_bottom[2] - color_top[2]) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))
    return gradient


def _draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: tuple,
    outline: tuple | None = None,
    width: int = 1,
) -> None:
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Wrap text to fit within max_width pixels."""
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = font.getbbox(test_line)
        if bbox[2] - bbox[0] <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines or [""]


def _draw_accent_bar(draw: ImageDraw.ImageDraw, y: int, width: int, color: tuple) -> None:
    """Draw a thin accent bar."""
    draw.rectangle([(60, y), (width - 60, y + 3)], fill=color)


def _draw_badge(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    bg_color: tuple,
    font: ImageFont.FreeTypeFont,
) -> int:
    """Draw a colored badge and return its width."""
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad_x, pad_y = 20, 10
    _draw_rounded_rect(
        draw,
        (x, y, x + tw + pad_x * 2, y + th + pad_y * 2),
        radius=8,
        fill=bg_color,
    )
    draw.text((x + pad_x, y + pad_y), text, fill=COLORS["text_white"], font=font)
    return tw + pad_x * 2


def _load_and_crop_artwork(
    image_path: str, target_width: int, target_height: int
) -> Image.Image | None:
    """Load artwork and crop to fit target aspect ratio without AI modification."""
    try:
        img = Image.open(image_path).convert("RGB")
    except (OSError, IOError) as exc:
        logger.warning("Cannot open artwork %s: %s", image_path, exc)
        return None

    src_w, src_h = img.size
    target_ratio = target_width / target_height
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        new_w = int(src_h * target_ratio)
        left = (src_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, src_h))
    else:
        new_h = int(src_w / target_ratio)
        top = (src_h - new_h) // 2
        img = img.crop((0, top, src_w, top + new_h))

    img = img.resize((target_width, target_height), Image.LANCZOS)
    return img


def _draw_branding_footer(draw: ImageDraw.ImageDraw, y_start: int) -> int:
    """Draw the OTTTracker branding footer. Returns the y position after footer."""
    y = y_start

    # Accent line
    _draw_accent_bar(draw, y, PIN_WIDTH, COLORS["accent_blue"])
    y += 20

    # Domain
    domain_font = _font(38, bold=True)
    draw.text((60, y), "OTTTRACKER.IN", fill=COLORS["text_white"], font=domain_font)
    y += 55

    # Tagline
    tag_font = _font(22)
    draw.text((60, y), "Find where to watch", fill=COLORS["text_muted"], font=tag_font)
    y += 40

    return y


def generate_pin(
    movie: dict,
    template_name: str,
    artwork_path: str | None = None,
    artwork_source: str = "branded_only",
    output_dir: str | None = None,
) -> tuple[str, str]:
    """Generate a Pinterest pin image.

    Args:
        movie: dict with keys: id, title, original_title, original_language,
               release_date, genres, ott_platform, ott_release_date, vote_average
        template_name: one of the TEMPLATES keys
        artwork_path: local path to poster/backdrop image (or None for branded-only)
        artwork_source: "poster", "backdrop", or "branded_only"
        output_dir: directory to save the pin image

    Returns:
        (output_path, relative_media_path)
    """
    template = TEMPLATES.get(template_name, TEMPLATES["NOW_STREAMING"])
    accent_color = COLORS[template["accent"]]
    badge_bg = template["badge_bg"]

    # ── Create base canvas ─────────────────────────────────────────────
    canvas = Image.new("RGB", (PIN_WIDTH, PIN_HEIGHT), COLORS["bg_dark"])

    # ── Place artwork if available ─────────────────────────────────────
    if artwork_path and artwork_source in ("poster", "backdrop"):
        artwork_img = _load_and_crop_artwork(artwork_path, PIN_WIDTH, PIN_HEIGHT)
        if artwork_img:
            # Blend artwork as background with dark overlay
            dark_overlay = Image.new("RGBA", (PIN_WIDTH, PIN_HEIGHT), (0, 0, 0, 120))
            canvas.paste(artwork_img, (0, 0))

            # Apply gradient overlay from bottom
            gradient = _gradient_overlay(
                PIN_WIDTH, PIN_HEIGHT,
                (0, 0, 0, 0),
                (15, 15, 25, 240),
            )
            canvas.paste(Image.alpha_composite(
                canvas.convert("RGBA"), gradient
            ).convert("RGB"), (0, 0))

            # Additional dark overlay at bottom for text readability
            bottom_overlay = Image.new("RGBA", (PIN_WIDTH, PIN_HEIGHT), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(bottom_overlay)
            overlay_draw.rectangle([(0, PIN_HEIGHT - 600), (PIN_WIDTH, PIN_HEIGHT)], fill=(15, 15, 25, 200))
            base_rgba = canvas.convert("RGBA")
            canvas = Image.alpha_composite(base_rgba, bottom_overlay).convert("RGB")
        else:
            # Fallback to branded-only layout
            artwork_source = "branded_only"
            gradient = _gradient_overlay(
                PIN_WIDTH, PIN_HEIGHT,
                COLORS["bg_gradient_top"],
                COLORS["bg_gradient_bottom"],
            )
            canvas.paste(gradient.convert("RGB"), (0, 0))
    else:
        # Branded-only: gradient background
        gradient = _gradient_overlay(
            PIN_WIDTH, PIN_HEIGHT,
            COLORS["bg_gradient_top"],
            COLORS["bg_gradient_bottom"],
        )
        canvas.paste(gradient.convert("RGB"), (0, 0))

    draw = ImageDraw.Draw(canvas)

    # ── Decorative elements for branded-only ───────────────────────────
    if artwork_source == "branded_only":
        # Abstract geometric decoration
        for i in range(5):
            x = 700 + i * 30
            y = 50 + i * 60
            size = 80 - i * 10
            draw.ellipse(
                [x, y, x + size, y + size],
                outline=(*accent_color[:3], 40),
                width=2,
            )
        # Diagonal accent line
        draw.line([(0, 200), (PIN_WIDTH, 100)], fill=(*accent_color, 30), width=2)

    # ── Top section: Badge ─────────────────────────────────────────────
    badge_font = _font(26, bold=True)
    _draw_badge(draw, template["badge"], 60, 60, badge_bg, badge_font)

    # ── Movie title ────────────────────────────────────────────────────
    title_y = 130
    title_font = _font(56, bold=True)
    title = movie.get("title", "Unknown")
    title_lines = _wrap_text(title, title_font, PIN_WIDTH - 120)
    for i, line in enumerate(title_lines[:3]):
        draw.text((60, title_y + i * 65), line, fill=COLORS["text_white"], font=title_font)
    title_y += len(title_lines[:3]) * 65 + 15

    # Original title (if different)
    original_title = movie.get("original_title")
    if original_title and original_title != title:
        orig_font = _font(28)
        draw.text((60, title_y), original_title, fill=COLORS["text_muted"], font=orig_font)
        title_y += 40

    # ── Metadata line ──────────────────────────────────────────────────
    meta_parts = []
    lang = movie.get("original_language")
    if lang:
        meta_parts.append(lang.upper())
    genres = movie.get("genres", [])
    if genres:
        meta_parts.extend(genres[:3])
    year = movie.get("release_date")
    if year:
        meta_parts.append(str(year)[:4])

    if meta_parts:
        meta_font = _font(24)
        meta_text = "  ·  ".join(meta_parts)
        draw.text((60, title_y), meta_text, fill=COLORS["text_light"], font=meta_font)
        title_y += 45

    # ── Accent bar ─────────────────────────────────────────────────────
    _draw_accent_bar(draw, title_y, PIN_WIDTH, accent_color)
    title_y += 25

    # ── Platform / OTT info card ───────────────────────────────────────
    ott_platform = movie.get("ott_platform")
    ott_date = movie.get("ott_release_date")

    if ott_platform or ott_date:
        card_y = title_y
        card_h = 130
        _draw_rounded_rect(
            draw,
            (60, card_y, PIN_WIDTH - 60, card_y + card_h),
            radius=12,
            fill=COLORS["card_bg"],
            outline=COLORS["card_border"],
            width=1,
        )

        if ott_platform:
            platform_font = _font(32, bold=True)
            draw.text((85, card_y + 15), ott_platform, fill=COLORS["text_white"], font=platform_font)

        if ott_date:
            date_label_font = _font(20)
            date_value_font = _font(26, bold=True)
            draw.text((85, card_y + 55), "OTT Release", fill=COLORS["text_muted"], font=date_label_font)
            draw.text((85, card_y + 80), str(ott_date), fill=accent_color, font=date_value_font)

        title_y = card_y + card_h + 25

    # ── Rating (optional, only if verified) ────────────────────────────
    vote_avg = movie.get("vote_average")
    if vote_avg and vote_avg > 0:
        rating_font = _font(28, bold=True)
        star_font = _font(24)
        rating_text = f"{vote_avg:.1f}"
        draw.text((60, title_y), rating_text, fill=COLORS["text_white"], font=rating_font)
        draw.text((130, title_y + 2), "/10", fill=COLORS["text_muted"], font=star_font)
        title_y += 45

    # ── CTA ────────────────────────────────────────────────────────────
    cta_y = max(title_y, PIN_HEIGHT - 280)
    cta_font = _font(28, bold=True)
    cta_text = "Find where to watch →"
    cta_bbox = cta_font.getbbox(cta_text)
    cta_w = cta_bbox[2] - cta_bbox[0]
    _draw_rounded_rect(
        draw,
        (60, cta_y, 60 + cta_w + 50, cta_y + 55),
        radius=27,
        fill=accent_color,
    )
    draw.text((85, cta_y + 10), cta_text, fill=COLORS["text_white"], font=cta_font)

    # ── Branding footer ────────────────────────────────────────────────
    _draw_branding_footer(draw, PIN_HEIGHT - 160)

    # ── Save ───────────────────────────────────────────────────────────
    movie_id = movie.get("id", 0)
    filename = f"pin-{movie_id}-{template_name.lower()}.png"

    if output_dir:
        out_path = Path(output_dir)
    else:
        out_path = Path(settings.MEDIA_ROOT) / "pinterest"
    out_path.mkdir(parents=True, exist_ok=True)

    filepath = out_path / filename
    canvas.save(filepath, "PNG", quality=95)

    relative_path = f"/media/pinterest/{filename}"
    logger.info("Generated pin: %s -> %s", movie_id, filepath)
    return str(filepath), relative_path


def generate_branded_only_pin(
    movie: dict,
    template_name: str,
    output_dir: str | None = None,
) -> tuple[str, str]:
    """Generate a pin using only typography and branding (no movie artwork)."""
    return generate_pin(
        movie=movie,
        template_name=template_name,
        artwork_path=None,
        artwork_source="branded_only",
        output_dir=output_dir,
    )
