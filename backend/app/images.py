"""Upload validation + resize pipeline (synchronous AI-friendly version).

Trimmed from the original ``app/services/image_service.py``:
  * kept   — format validation, megapixel ceiling, JPEG EXIF-aware orientation,
             thumbnail/medium generation, HEIC passthrough rejection.
  * dropped — pHash duplicate detection (imagehash), background removal
             (rembg / external HTTP), rotate, multi-image galleries, and the
             arq job that used to drive all of it. Those became a whole
             second worker container in the original; the trial has none.
"""

import io
import logging
import uuid
from pathlib import PurePosixPath
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings

logger = logging.getLogger(__name__)


def _settings():
    """Accessed, not snapshotted: a module-level copy would freeze the env at import."""
    return get_settings()


SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}
_ALLOWED_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
_EXT_BY_FORMAT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


class ImageValidationError(ValueError):
    """Raised for anything the user can fix by re-picking a file."""


class ImageTooLargeError(ImageValidationError):
    def __init__(self, pixels: float, limit: float) -> None:
        super().__init__(
            f"That image is too large ({pixels:.0f} megapixels). "
            f"Please upload one under {limit:.0f} megapixels."
        )


def validate_content_type(content_type: str | None) -> None:
    if content_type and content_type.lower() not in _ALLOWED_TYPES:
        raise ImageValidationError(
            f"Unsupported file type '{content_type}'. Upload a JPEG, PNG, or WebP photo."
        )


def _open(image_bytes: bytes, *, draft_ok: bool = True) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except UnidentifiedImageError as exc:
        raise ImageValidationError(
            "That file isn't a readable image. Export it as JPEG or PNG and try again."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - Pillow raises many decode-time types
        raise ImageValidationError(f"Could not decode that image: {exc}") from exc

    if img.format not in SUPPORTED_FORMATS:
        raise ImageValidationError(
            f"{img.format or 'Unknown'} images aren't supported. "
            "Convert it to JPEG or PNG (iPhone HEIC needs exporting first)."
        )

    width, height = img.size
    megapixels = (width * height) / 1_000_000
    if megapixels > _settings().max_image_megapixels:
        # A draft decode gives an exact size cheaply before we reject.
        raise ImageTooLargeError(megapixels, _settings().max_image_megapixels)
    if draft_ok and img.format == "JPEG":
        img.draft("RGB", (min(width, 2400), min(height, 2400)))
    return img


def process_upload(image_bytes: bytes, content_type: str | None) -> dict[str, Any]:
    """Return ``{"original": bytes, "thumbnail": bytes, "medium": bytes|None}``.

    JPEG is the universal output: it is what the AI preprocessing expects and
    it keeps the ephemeral disk (and any bucket) small.
    """
    if len(image_bytes) > _settings().max_upload_size_mb * 1024 * 1024:
        raise ImageValidationError(
            f"That file is {len(image_bytes) / 1_048_576:.1f} MB. "
            f"Uploads are capped at {_settings().max_upload_size_mb} MB."
        )

    validate_content_type(content_type)
    img = _open(image_bytes)
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    quality = _settings().image_quality

    def _encode(image: Image.Image, max_side: int | None) -> bytes:
        out = image
        if max_side and max(out.size) > max_side:
            out = out.copy()
            out.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()

    original = _encode(img, _settings().medium_size * 3)
    thumbnail = _encode(img, _settings().thumbnail_size)
    medium = (
        _encode(img, _settings().medium_size)
        if max(img.size) > _settings().thumbnail_size
        else None
    )
    return {"original": original, "thumbnail": thumbnail, "medium": medium}


def new_keys(prefix: str, ext: str = ".jpg") -> dict[str, str]:
    base = f"{prefix}/{uuid.uuid4().hex}{ext}"
    stem = PurePosixPath(base).with_suffix("")
    return {
        "original": str(stem) + ".jpg",
        "thumbnail": f"{stem}_thumb.jpg",
        "medium": f"{stem}_medium.jpg",
    }


def analyze_target_bytes(variants: dict[str, Any]) -> bytes:
    """The bytes handed to the vision model (thumbnail sized, already JPEG)."""
    return variants["thumbnail"]
