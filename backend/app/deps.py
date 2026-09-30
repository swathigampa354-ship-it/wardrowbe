"""Shared helpers: item serialization and upload-to-analysis, used by both the
single-upload and bulk-upload paths so their failure handling cannot drift.
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_service import AIError, ClothingTags, get_ai
import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from typing import AsyncIterator
from app.config import get_settings
from app.images import ImageValidationError, new_keys, process_upload
from app.models import ClothingItem
from app.schemas import ItemResponse
from app.storage import get_store, image_url

logger = logging.getLogger(__name__)


def _settings():
    return get_settings()


def build_item_response(item: ClothingItem) -> ItemResponse:
    data: dict[str, Any] = {
        "id": item.id,
        "user_id": item.user_id,
        "type": item.type,
        "subtype": item.subtype,
        "name": item.name,
        "brand": item.brand,
        "notes": item.notes,
        "favorite": bool(item.favorite),
        "image_path": item.image_key or "",
        "thumbnail_path": item.thumbnail_key,
        "medium_path": item.medium_key,
        "original_image_path": item.original_key,
        "image_url": image_url(item.image_key, "original"),
        "thumbnail_url": image_url(item.thumbnail_key or item.image_key, "thumbnail"),
        "medium_url": image_url(item.medium_key or item.image_key, "medium"),
        "tags": item.tags or {},
        "colors": item.colors or [],
        "primary_color": item.primary_color,
        "pattern": item.pattern,
        "material": item.material,
        "formality": item.formality,
        "fit": item.fit,
        "style": item.style or [],
        "season": item.season or [],
        "status": item.status,
        "ai_processed": bool(item.ai_processed),
        "ai_confidence": float(item.ai_confidence) if item.ai_confidence is not None else None,
        "ai_description": item.ai_description,
        "ai_error": item.ai_error,
        "ai_unrecognized_type": item.ai_unrecognized_type,
        "ai_started_at": item.ai_started_at,
        "ai_completed_at": item.ai_completed_at,
        "processing_kind": None,
        "tagging_status": item.tags.get("tagging_status", "tagged")
        if isinstance(item.tags, dict)
        else "tagged",
        "tagged_by": item.tags.get("tagged_by") if isinstance(item.tags, dict) else None,
        "tagged_at": item.ai_completed_at,
        "wear_count": item.wear_count or 0,
        "last_worn_at": item.last_worn_at,
        "last_suggested_at": item.last_suggested_at,
        "suggestion_count": item.suggestion_count or 0,
        "acceptance_count": item.acceptance_count or 0,
        "is_archived": bool(item.is_archived),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    return ItemResponse(**data)


def apply_tags(item: ClothingItem, tags: ClothingTags) -> None:
    """Write AI tags onto the row. Mutating a JSON column needs flag_modified."""

    from sqlalchemy.orm.attributes import flag_modified

    item.type = tags.type
    item.subtype = tags.subtype
    item.primary_color = tags.primary_color
    item.colors = tags.colors
    item.pattern = tags.pattern
    item.material = tags.material
    item.formality = tags.formality
    item.fit = tags.fit
    item.style = tags.style
    item.season = tags.season
    item.ai_description = tags.description
    item.ai_confidence = tags.logprobs_confidence if tags.logprobs_confidence is not None else tags.confidence
    item.ai_processed = True
    item.ai_unrecognized_type = tags.unrecognized_type
    item.status = "ready"
    item.tags = {
        "occasion": tags.occasion,
        "completeness": tags.completeness(),
        "tagging_status": "tagged",
        "tagged_by": "auto",
        "raw": (tags.raw_response or "")[:1500] or None,
    }
    flag_modified(item, "tags")


def apply_manual_edits(item: ClothingItem, fields: dict[str, Any]) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    tag_updates = {}
    for key, value in list(fields.items()):
        if value is None:
            continue
        if key == "notes" or key.endswith("_untouched"):
            continue
        setattr(item, key, value)
        if key in {"type", "subtype", "primary_color", "pattern", "material", "formality", "fit"}:
            tag_updates[key] = value
    if tag_updates:
        item.tags = {**(item.tags or {}), "manual_edits": tag_updates, "tagged_by": "manual"}
        flag_modified(item, "tags")


#: Serializes SQLite write transactions across concurrent tasks.
#:
#: store_and_analyze() keeps its transaction open across a (slow) AI call, so N
#: parallel uploads means N overlapping writers - and SQLite admits exactly one,
#: turning the rest into "database is locked". The lock costs nothing where it is
#: not needed (a single upload, or Postgres, which really can run writers
#: concurrently) and removes the failure where it is.
#:
#: It is *reentrant per task*, because a request handled by Depends(get_db) may
#: legitimately take the lock itself before calling a helper that also takes it.
_LOCK_HOLDER: "asyncio.Task | None" = None
_LOCK_DEPTH = 0
_LOCK_EVENT: "asyncio.Event | None" = None


@asynccontextmanager
async def writer_lock() -> AsyncGenerator[None, None]:
    """Hold the process-wide SQLite writer slot (reentrant within one task)."""
    global _LOCK_HOLDER, _LOCK_DEPTH, _LOCK_EVENT
    me = asyncio.current_task()
    if _LOCK_HOLDER is me:
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
        return

    event = _LOCK_EVENT or asyncio.Event()
    _LOCK_EVENT = event
    while _LOCK_HOLDER is not None:
        await event.wait()
    _LOCK_HOLDER, _LOCK_DEPTH = me, 1
    event.clear()
    try:
        yield
    finally:
        _LOCK_DEPTH -= 1
        if _LOCK_DEPTH == 0:
            _LOCK_HOLDER = None
            event.set()


async def store_and_analyze(
    db: AsyncSession,
    *,
    user_id: str,
    image_bytes: bytes,
    content_type: str | None,
    name: str | None = None,
    skip_ai: bool = False,
    upload_key: str | None = None,
    filename: str = "upload.jpg",
    defer_analysis: bool = False,
) -> tuple[ClothingItem, str | None]:
    """Persist an upload, then analyze it inline. Returns (item, ai_error_message).

    The item is committed even if the AI call fails, with ``status='error'`` and
    a human-readable ``ai_error``: losing a user's photo because a model timed
    out is worse than a wardrobe row they can re-analyze.

    With ``defer_analysis`` the row is committed as ``processing`` and the AI
    call is left to :func:`analyze_item_row`, so the caller can release the
    write transaction (and this session) before the slow part starts. The bulk
    endpoint uses that to answer in milliseconds while the tags land a few
    seconds later, exactly like the queue-backed original did.
    """
    variants = process_upload(image_bytes, content_type)  # raises ImageValidationError
    keys = new_keys(prefix=f"users/{user_id}/items")

    store = get_store()
    for kind in ("original", "thumbnail", "medium"):
        blob = variants.get(kind)
        if blob is None:
            continue
        await store.put(keys[kind], blob, "image/jpeg")

    item = ClothingItem(
        user_id=user_id,
        image_key=keys["original"],
        thumbnail_key=keys["thumbnail"],
        medium_key=keys["medium"] if variants.get("medium") else None,
        original_key=None,
        upload_key=upload_key,
        type="unknown",
        name=name or (filename.rsplit(".", 1)[0][:60] if filename else None),
        status="processing",
        tags={},
    )
    db.add(item)
    await db.flush()

    if defer_analysis:
        # Row committed; the AI call belongs to analyze_item_row() so the
        # transaction above can close before the slow network round-trip.
        item.tags = {**(item.tags or {}), "tagging_status": "pending"}
        await db.flush()
        return item, None

    ai_error: str | None = None
    if skip_ai:
        item.status = "ready"
        item.type = "unknown"
        item.tags = {**(item.tags or {}), "tagging_status": "pending", "skipped_ai": True}
    elif not _settings().ai_configured:
        ai_error = (
            "Saved without AI analysis: AI_BASE_URL is not configured on this server, "
            "so tags can't be generated. Fill in the details manually or set the AI env vars."
        )
        item.status = "error"
        item.ai_error = ai_error
        item.tags = {**(item.tags or {}), "tagging_status": "pending"}
    else:
        from datetime import UTC, datetime

        item.ai_started_at = datetime.now(UTC)
        # Built before the try so a provider error is reported, but a broken AI
        # configuration can never be mistaken for a lost upload.
        ai = get_ai()
        try:
            tags = await ai.analyze_image_bytes(variants["thumbnail"])
            apply_tags(item, tags)
            item.ai_completed_at = datetime.now(UTC)
        except AIError as exc:
            ai_error = str(exc)
            logger.warning("Analysis failed for %s: %s", filename, exc)
            item.status = "error"
            item.ai_error = ai_error[:500]
            item.tags = {**(item.tags or {}), "tagging_status": "pending"}
        except ImageValidationError as exc:
            ai_error = str(exc)
            item.status = "error"
            item.ai_error = ai_error[:500]
        except Exception as exc:  # noqa: BLE001 - surfaced as a readable message
            logger.exception("Unexpected analysis failure for %s", filename)
            ai_error = f"Analysis failed unexpectedly: {exc}"
            item.status = "error"
            item.ai_error = ai_error[:500]

    await db.flush()
    return item, ai_error


@asynccontextmanager
async def writing_session() -> AsyncGenerator[AsyncSession, None]:
    """A session whose whole lifetime (including commit) holds WRITE_LOCK.

    Committing is the moment SQLite actually takes the write lock, so a session
    that releases the lock *before* committing re-creates the contention the
    lock exists to avoid. Every self-managed session in the app goes through
    here; request-scoped sessions get the same treatment inside get_db().
    """
    from app.database import get_session_maker

    async with writer_lock(), get_session_maker()() as session, session.begin():
        yield session


async def analyze_item_row(item_id: str) -> str | None:
    """Analyze an already-persisted item in its own session (background task).

    Only ever call this from a task that is *not* a request handler: it takes
    the writer slot itself, and a request already holds it for its whole life
    (app/database.py:get_db) - the same task would wait on itself forever.
    

    Returns the AI error message, or None. Any failure is stored on the row as
    ``status='error'`` + ``ai_error`` rather than raised: nobody is awaiting
    this call, so a raised error would only end up in the logs.
    """
    from app.models import ClothingItem

    from app.storage import get_store

    async with writing_session() as session:
        item = await session.get(ClothingItem, item_id)
        if item is None:  # deleted while the upload response was in flight
            return None
        stored = await get_store().get(item.image_key or "")
        if stored is None:
            item.status = "error"
            item.ai_error = (
                "The photo was no longer in storage when the analysis ran. "
                "Re-upload it, or point STORAGE_DIR/S3 at something durable."
            )
            item.tags = {**(item.tags or {}), "tagging_status": "pending"}
            return item.ai_error

        from datetime import UTC, datetime

        item.ai_started_at = datetime.now(UTC)
        try:
            tags = await get_ai().analyze_image_bytes(stored[0])
            apply_tags(item, tags)
            item.ai_completed_at = datetime.now(UTC)
            item.ai_error = None
        except AIError as exc:
            logger.warning("Background analysis failed for %s: %s", item_id, exc)
            item.status = "error"
            item.ai_error = str(exc)[:500]
            item.tags = {**(item.tags or {}), "tagging_status": "pending"}
            return item.ai_error
        except Exception as exc:  # noqa: BLE001 - surfaced on the row
            logger.exception("Unexpected background analysis failure for %s", item_id)
            item.status = "error"
            item.ai_error = f"Analysis failed unexpectedly: {exc}"[:500]
            item.tags = {**(item.tags or {}), "tagging_status": "pending"}
            return item.ai_error
        return None


async def requeue_orphaned_analysis() -> int:
    """Finish (or fail) rows left ``processing`` by a restart.

    Render Free restarts the container on every deploy and idles it to sleep, so
    an in-flight analysis dies with it. Without this the wardrobe shows a
    spinner forever and the frontend polls a queue that no longer exists.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select, update

    from app.models import ClothingItem

    async with writing_session() as session:
            ids = list(
                (
                    await session.execute(
                        select(ClothingItem.id).where(ClothingItem.status == "processing")
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                return 0
            await session.execute(
                update(ClothingItem)
                .where(ClothingItem.id.in_(ids))
                .values(
                    status="error",
                    ai_error=(
                        "Analysis was interrupted when the server restarted. "
                        "Use Re-analyze to try again."
                    ),
                    ai_completed_at=datetime.now(UTC),
                )
            )
            return len(ids)


__all__ = [
    "AIError",
    "ImageValidationError",
    "analyze_item_row",
    "apply_manual_edits",
    "apply_tags",
    "build_item_response",
    "requeue_orphaned_analysis",
    "store_and_analyze",
    "writing_session",
]
