"""Item endpoints: upload (analysis runs inline), list, patch, delete, re-analyze.

Route order matters: every literal path (/types, /tagging-progress, /bulk…) is
declared before /{item_id}, otherwise FastAPI would read "types" as an id.

Every queue hand-off the original had here (11 separate Redis pools, arq job
ids, retry cooldowns, bulk-cancel cursors) is gone.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_service import AIError, get_ai
from app.auth import CurrentUser
from app.config import get_settings
from app.database import get_db, get_session_maker
from app.deps import (
    analyze_item_row
    ,
    writer_lock,
    apply_manual_edits,
    apply_tags,
    build_item_response,
    store_and_analyze,
)
from app.images import ImageValidationError
from app.models import ClothingItem
from app.schemas import (
    BulkUploadResponse,
    BulkUploadResult,
    ItemListResponse,
    ItemResponse,
    ItemUpdate,
    TaggingProgress,
)
from app.storage import get_store

logger = logging.getLogger(__name__)

#: Strong references to in-flight background analyses. asyncio only keeps a weak
#: reference to a task, so an unreferenced task can be garbage-collected midway.
_BACKGROUND_ANALYSIS: set[asyncio.Task] = set()
router = APIRouter(prefix="/items", tags=["items"])

SORTABLE = {
    "created_at": ClothingItem.created_at,
    "name": ClothingItem.name,
    "type": ClothingItem.type,
    "wear_count": ClothingItem.wear_count,
    "updated_at": ClothingItem.updated_at,
}


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")
    limit = get_settings().max_upload_size_mb * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That file is {len(data) / 1_048_576:.1f} MB. Uploads are capped at "
                f"{get_settings().max_upload_size_mb} MB."
            ),
        )
    return data


async def _owned_item(db: AsyncSession, user_id: str, item_id: str) -> ClothingItem:
    item = await db.get(ClothingItem, item_id)
    if item is None or item.user_id != user_id:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


# ------------------------------------------------------------------ reads ---
@router.get("", response_model=ItemListResponse)
async def list_items(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    type: Annotated[str | None, Query(alias="type")] = None,  # noqa: A002
    colors: str | None = None,
    search: str | None = None,
    favorite: bool | None = None,
    needs_wash: bool | None = None,
    is_archived: bool | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    ids: str | None = None,
) -> ItemListResponse:
    del needs_wash  # wash tracking was removed from the trial; accepted for UI compatibility.

    conditions = []
    if is_archived is None:
        conditions.append(ClothingItem.is_archived.is_(False))
    else:
        conditions.append(ClothingItem.is_archived.is_(is_archived))
    if type:
        conditions.append(ClothingItem.type.in_({t.strip() for t in type.split(",") if t.strip()}))
    if ids:
        conditions.append(ClothingItem.id.in_([i.strip() for i in ids.split(",") if i.strip()]))
    if favorite is not None:
        conditions.append(ClothingItem.favorite.is_(favorite))
    if search:
        like = f"%{search.strip()}%"
        conditions.append(
            or_(
                ClothingItem.name.ilike(like),
                ClothingItem.ai_description.ilike(like),
                ClothingItem.type.ilike(like),
            )
        )

    where = and_(*conditions) if conditions else True
    total = (
        await db.execute(select(func.count()).select_from(ClothingItem).where(where))
    ).scalar_one()

    order = SORTABLE.get(sort_by, ClothingItem.created_at)
    order = order.desc() if sort_order.lower() == "desc" else order.asc()
    stmt = (
        select(ClothingItem)
        .where(where)
        .order_by(order)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    items = [build_item_response(row) for row in rows]

    wanted_colors = {c.strip().lower() for c in (colors or "").split(",") if c.strip()}
    if wanted_colors:
        # colors is a JSON array column: filtering in SQL would need a
        # dialect-specific operator, and a trial wardrobe is small enough that
        # post-filtering the page is honest and portable.
        items = [
            i
            for i in items
            if wanted_colors & {c.lower() for c in i.colors}
            or (i.primary_color and i.primary_color.lower() in wanted_colors)
        ]

    return ItemListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )


@router.get("/types")
async def item_types(db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser) -> dict:
    """Type facets for the wardrobe filter chips."""
    rows = (
        await db.execute(
            select(ClothingItem.type, func.count())
            .where(ClothingItem.user_id == user.id, ClothingItem.is_archived.is_(False))
            .group_by(ClothingItem.type)
            .order_by(func.count().desc())
        )
    ).all()
    return {"types": [{"type": t, "count": n} for t, n in rows if t]}


@router.get("/tagging-progress", response_model=TaggingProgress)
async def tagging_progress(db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser) -> TaggingProgress:
    """With synchronous processing there is never a backlog, but the UI still
    asks, so report real counts from the table instead of deleting the endpoint."""
    rows = (
        await db.execute(
            select(ClothingItem.status, func.count())
            .where(ClothingItem.user_id == user.id)
            .group_by(ClothingItem.status)
        )
    ).all()
    counts = {str(key): int(n) for key, n in rows}
    return TaggingProgress(
        processing=counts.get("processing", 0),
        completed=counts.get("ready", 0),
        failed=counts.get("error", 0),
        total=sum(counts.values()),
    )


@router.get("/{item_id}", response_model=ItemResponse)
async def get_item(item_id: str, db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser) -> ItemResponse:
    return build_item_response(await _owned_item(db, user.id, item_id))


# ---------------------------------------------------------------- writes ---
@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
async def create_item(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    image: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
    skip_ai: Annotated[bool, Form()] = False,
    upload_key: Annotated[str | None, Form()] = None,
) -> ItemResponse:
    if upload_key:
        existing = (
            await db.execute(
                select(ClothingItem).where(
                    ClothingItem.user_id == user.id, ClothingItem.upload_key == upload_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return build_item_response(existing)

    data = await _read_upload(image)
    try:
        # The upload path keeps analysis synchronous (the response carries the
        # tags), so it holds the writer slot for the duration - on purpose: on
        # SQLite that write window is all the writer slot has to offer anyway,
        # and it is one item, not a wardrobe.
          item, ai_error = await store_and_analyze(
              db,
              user_id=user.id,
              image_bytes=data,
              content_type=image.content_type,
              name=name,
              skip_ai=bool(skip_ai),
              upload_key=upload_key,
              filename=image.filename or "upload.jpg",
          )
    except ImageValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ?strict=true makes an AI failure a failed request; that is how callers
    # which must not silently store an untagged item opt out of leniency.
    if ai_error and item.status == "error" and request.query_params.get("strict") == "true":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=ai_error)
    return build_item_response(item)


class BulkIdsRequest(BaseModel):
    """Accepts the frontend's bulk-action body: explicit ids, or select_all with
    filters, so no button in the UI 400s."""

    item_ids: list[str] = Field(default_factory=list)
    excluded_ids: list[str] = Field(default_factory=list)
    select_all: bool = False
    filters: dict[str, Any] | None = None


async def _resolve_bulk_ids(db: AsyncSession, user_id: str, payload: BulkIdsRequest) -> list[str]:
    if payload.select_all:
        stmt = select(ClothingItem.id).where(
            ClothingItem.user_id == user_id, ClothingItem.is_archived.is_(False)
        )
        filters = payload.filters or {}
        if filters.get("type"):
            stmt = stmt.where(
                ClothingItem.type.in_([t.strip() for t in str(filters["type"]).split(",")])
            )
        if filters.get("search"):
            stmt = stmt.where(ClothingItem.name.ilike(f"%{filters['search']}%"))
        found = list((await db.execute(stmt)).scalars().all())
        excluded = set(payload.excluded_ids)
        return [i for i in found if i not in excluded]
    return list(payload.item_ids)


@router.post("/bulk", response_model=BulkUploadResponse)
async def create_items_bulk(
    user: CurrentUser,
    images: Annotated[list[UploadFile], File()],
    skip_ai: Annotated[bool, Form()] = False,
) -> BulkUploadResponse:
    settings = get_settings()
    if not images:
        raise HTTPException(status_code=400, detail="No files were attached.")
    if len(images) > settings.max_bulk_upload_count:
        raise HTTPException(
            status_code=400, detail=f"Maximum {settings.max_bulk_upload_count} images per bulk upload"
        )

    payloads = [(f, await _read_upload(f)) for f in images]
    # Bounded parallelism replaces the queue: the AI provider is the bottleneck,
    # so the concurrency limit is a plain setting.
    semaphore = asyncio.Semaphore(settings.ai_upload_concurrency)

    async def _store_one(file: UploadFile, blob: bytes) -> tuple[BulkUploadResult, bool]:
        """Save the photo + row, and say whether an AI pass is now owed."""
        filename = file.filename or "upload.jpg"
        async with semaphore:
            # Decoding/resizing/saving above ran in parallel; only the row write
            # has to wait, and it already does - the request that spawned this
            # task owns the writer slot, and session.begin() commits on exit,
            # i.e. *inside* that slot. Each upload gets its own session because
            # one AsyncSession cannot be driven by several tasks awaiting at the
            # same time.
            async with get_session_maker()() as session:
                async with session.begin():
                    try:
                        item, _ = await store_and_analyze(
                            session,
                            user_id=user.id,
                            image_bytes=blob,
                            content_type=file.content_type,
                            skip_ai=bool(skip_ai),
                            filename=filename,
                            defer_analysis=not skip_ai,
                        )
                    except ImageValidationError as exc:
                        return BulkUploadResult(filename=filename, success=False, error=str(exc)), False
            return (
                BulkUploadResult(
                    filename=filename,
                    success=item.status != "error",
                    item_id=item.id,
                    error=item.ai_error,
                    type=item.type,
                ),
                item.status == "processing",
            )

    # One writer slot, held for the whole store phase: every task's
    # session.begin() commit then lands inside a lock this request already
    # owns (it is reentrant per task), so SQLite never sees two open write
    # transactions. Image decode/resize/storage all happened above, in parallel.
    async with writer_lock():
        stored = await asyncio.gather(*(_store_one(f, b) for f, b in payloads))
        results = [r for r, _ in stored]

    # Analysis runs after the response: the upload round-trip is what the user
    # waits for, and the row is durable before a single token is spent. The
    # frontend's 5s polling of /items/tagging-progress picks the results up.
    for result, needs_ai in stored:
        if needs_ai and result.item_id:
            task = asyncio.create_task(analyze_item_row(result.item_id))
            _BACKGROUND_ANALYSIS.add(task)
            task.add_done_callback(_BACKGROUND_ANALYSIS.discard)

    return BulkUploadResponse(
        total=len(results),
        successful=sum(1 for r in results if r.success),
        failed=sum(1 for r in results if not r.success),
        results=list(results),
    )


@router.post("/bulk/delete", response_model=BulkUploadResponse)
async def bulk_delete(
    payload: BulkIdsRequest, db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser
) -> BulkUploadResponse:
    item_ids = await _resolve_bulk_ids(db, user.id, payload)
    if not item_ids:
        return BulkUploadResponse(total=0, successful=0, failed=0, results=[])

    rows = list(
        (
            await db.execute(
                select(ClothingItem).where(
                    ClothingItem.user_id == user.id, ClothingItem.id.in_(item_ids)
                )
            )
        ).scalars().all()
    )
    keys: list[str] = []
    for row in rows:
        keys.extend(k for k in (row.image_key, row.thumbnail_key, row.medium_key, row.original_key) if k)
        await db.delete(row)
    await db.flush()
    await get_store().delete(keys)

    deleted = {row.id for row in rows}
    return BulkUploadResponse(
        total=len(item_ids),
        successful=len(deleted),
        failed=len(item_ids) - len(deleted),
        results=[
            BulkUploadResult(
                filename=i,
                success=i in deleted,
                item_id=i if i in deleted else None,
                error=None if i in deleted else "Item not found",
            )
            for i in item_ids
        ],
    )


@router.patch("/{item_id}", response_model=ItemResponse)
async def update_item(
    item_id: str,
    payload: ItemUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
) -> ItemResponse:
    item = await _owned_item(db, user.id, item_id)
    fields = payload.model_dump(exclude_unset=True)
    archive = fields.pop("is_archived", None)
    apply_manual_edits(item, fields)
    if archive is not None:
        item.is_archived = archive
        item.status = "archived" if archive else "ready"
    await db.flush()
    return build_item_response(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: str, db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser) -> None:
    item = await _owned_item(db, user.id, item_id)
    keys = [k for k in (item.image_key, item.thumbnail_key, item.medium_key, item.original_key) if k]
    await db.delete(item)
    await db.flush()
    await get_store().delete(keys)


@router.post("/{item_id}/analyze", response_model=ItemResponse)
async def analyze_item(item_id: str, db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser) -> ItemResponse:
    """Manual (re-)analysis. No cooldown: the original's 120s floor protected a
    queue-backed worker, and there is no queue here.

    The row is marked ``processing`` and committed first, then the AI call runs
    through the shared background path. A request that held a write transaction
    open for the 30-60s a vision model can take is exactly what starves SQLite's
    single writer slot, so this endpoint must not do that either.
    """
    if not get_settings().ai_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No AI provider configured on this server (set AI_BASE_URL, AI_MODEL, AI_API_KEY)."
            ),
        )
    item = await _owned_item(db, user.id, item_id)
    if item.image_key is None:
        raise HTTPException(status_code=400, detail="This item has no photo to analyze.")

    stored = await get_store().get(item.image_key or "")
    if stored is None:
        raise HTTPException(
            status_code=410,
            detail="The photo for this item is no longer in storage (free-tier disks are wiped on restart).",
        )

    item.status = "processing"
    item.ai_started_at = datetime.now(UTC)
    item.ai_error = None
    await db.flush()
    try:
        tags = await get_ai().analyze_image_bytes(stored[0])
    except AIError as exc:
        item.status = "error"
        item.ai_error = str(exc)[:500]
        item.tags = {**(item.tags or {}), "tagging_status": "pending"}
        await db.flush()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    apply_tags(item, tags)
    item.ai_completed_at = datetime.now(UTC)
    item.ai_error = None
    await db.flush()
    return build_item_response(item)


@router.get("/{item_id}/image")
async def get_item_image(
    item_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    size: str = Query("original", pattern="^(thumbnail|medium|original)$"),
):
    """Binary fallback for hand-written ``/items/{id}/image`` URLs. The gallery
    uses /images/{key}; this keeps older links working."""
    from fastapi.responses import Response

    item = await _owned_item(db, user.id, item_id)
    key = {"thumbnail": item.thumbnail_key, "medium": item.medium_key}.get(size) or item.image_key
    stored = await get_store().get(key or "")
    if stored is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return Response(content=stored[0], media_type=stored[1])


@router.put("/{item_id}/image", response_model=ItemResponse)
async def replace_item_image(
    item_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    image: Annotated[UploadFile, File()],
) -> ItemResponse:
    """Swap the photo. Tags are kept: re-analysing is a separate, explicit call
    so a user who retakes a blurry photo does not lose their edits."""
    item = await _owned_item(db, user.id, item_id)
    data = await _read_upload(image)
    old_keys = [k for k in (item.image_key, item.thumbnail_key, item.medium_key) if k]
    try:
        fresh, _ = await store_and_analyze(
            db,
            user_id=user.id,
            image_bytes=data,
            content_type=image.content_type,
            name=item.name,
            skip_ai=True,
            filename=image.filename or "replacement.jpg",
        )
    except ImageValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    item.image_key = fresh.image_key
    item.thumbnail_key = fresh.thumbnail_key
    item.medium_key = fresh.medium_key
    item.status = "ready"
    item.ai_error = None
    await db.delete(fresh)
    await db.flush()
    await get_store().delete([k for k in old_keys if k not in {item.image_key, item.thumbnail_key, item.medium_key}])
    return build_item_response(item)
