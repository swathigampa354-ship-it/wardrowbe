"""Outfit endpoints for the trial: generate 3 options, list history, act on one.

Removed relative to the original 1525-line router: scheduled/notification
outfits, family ratings, lookbook cloning, "wore instead" forks, studio
authoring, replacement chains, and the analytics-side counters.
"""

import logging
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_service import AIError
from app.auth import CurrentUser
from app.config import get_settings
from app.database import get_db
from app.models import ClothingItem, Outfit, OutfitItem, layer_of
from app.outfit_service import NotEnoughItemsError, generate_outfits
from app.schemas import (
    FeedbackRequest,
    OutfitItemResponse,
    OutfitListResponse,
    OutfitResponse,
    SuggestRequest,
)
from app.storage import image_url
from app.weather import current_weather

logger = logging.getLogger(__name__)


def _settings():
    """Accessed, not snapshotted: a module-level copy would freeze the env at import."""
    return get_settings()


router = APIRouter(prefix="/outfits", tags=["outfits"])


def _item_payload(item: ClothingItem, position: int) -> OutfitItemResponse:
    return OutfitItemResponse(
        id=item.id,
        type=item.type,
        subtype=item.subtype,
        name=item.name,
        primary_color=item.primary_color,
        colors=item.colors or [],
        image_path=item.image_key or "",
        thumbnail_path=item.thumbnail_key,
        image_url=image_url(item.image_key, "original"),
        thumbnail_url=image_url(item.thumbnail_key or item.image_key, "thumbnail"),
        layer_type=layer_of(item.type),
        position=position,
    )


async def build_outfit_response(db: AsyncSession, outfit: Outfit) -> OutfitResponse:
    rows = (
        await db.execute(
            select(ClothingItem, OutfitItem.position)
            .join(OutfitItem, OutfitItem.item_id == ClothingItem.id)
            .where(OutfitItem.outfit_id == outfit.id)
            .order_by(OutfitItem.position)
        )
    ).all()

    total_items = (
        await db.execute(
            select(func.count())
            .select_from(ClothingItem)
            .where(ClothingItem.user_id == outfit.user_id)
        )
    ).scalar_one()

    palette = [i.primary_color for i, _ in rows if i.primary_color]
    payload: dict[str, Any] = {
        "id": outfit.id,
        "occasion": outfit.occasion,
        "scheduled_for": outfit.scheduled_for,
        "status": outfit.status,
        "source": outfit.source,
        "name": outfit.headline or outfit.name,
        "headline": outfit.headline,
        "reasoning": outfit.reasoning,
        "style_notes": outfit.style_notes,
        "highlights": outfit.highlights or [],
        "season": outfit.season,
        "formality": outfit.formality,
        "palette": sorted(set(palette)),
        "notes": None,
        "weather": outfit.weather_data,
        "items": [_item_payload(item, pos) for item, pos in rows],
        "feedback": outfit.feedback,
        "is_starter_suggestion": total_items <= 5,
        "created_at": outfit.created_at,
    }
    return OutfitResponse(**payload)


async def _owned_outfit(db: AsyncSession, user_id: str, outfit_id: str) -> Outfit:
    outfit = await db.get(Outfit, outfit_id)
    if outfit is None or outfit.user_id != user_id:
        raise HTTPException(status_code=404, detail="Outfit not found")
    return outfit


@router.post("/suggest", response_model=OutfitResponse)
async def suggest_one(
    payload: SuggestRequest, db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser
) -> OutfitResponse:
    outfits = await _generate(db, user.id, payload, count=1)
    return await build_outfit_response(db, outfits[0])


@router.post("/suggest-options", response_model=list[OutfitResponse])
async def suggest_options(
    payload: SuggestRequest, db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser
) -> list[OutfitResponse]:
    """The endpoint the existing /dashboard/suggest page calls: 3 complete looks."""
    outfits = await _generate(db, user.id, payload, count=3)
    return [await build_outfit_response(db, outfit) for outfit in outfits]


async def _generate(
    db: AsyncSession, user_id: str, payload: SuggestRequest, *, count: int
) -> list[Outfit]:
    weather_payload: dict[str, Any] | None = None
    if payload.weather_override:
        w = payload.weather_override
        weather_payload = {
            "temperature": w.temperature,
            "feels_like": w.feels_like if w.feels_like is not None else w.temperature,
            "humidity": w.humidity,
            "precipitation_chance": w.precipitation_chance,
            "condition": w.condition,
        }
    elif _settings().weather_enabled:
        weather_payload = await current_weather(db, user_id)

    if not _settings().ai_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No AI provider configured on this server. Set AI_BASE_URL, AI_MODEL and "
                "AI_API_KEY, then redeploy."
            ),
        )

    try:
        return await generate_outfits(
            db,
            user_id=user_id,
            occasion=payload.occasion or "casual",
            weather_payload=weather_payload,
            include_items=payload.include_items,
            exclude_items=payload.exclude_items,
            count=count,
        )
    except NotEnoughItemsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except AIError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get("", response_model=OutfitListResponse)
async def list_outfits(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    occasion: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
) -> OutfitListResponse:
    del search  # nothing to full-text match on now that notes/family ratings are gone
    conditions = [Outfit.user_id == user.id]
    if status_filter:
        conditions.append(
            Outfit.status.in_([s.strip() for s in status_filter.split(",") if s.strip()])
        )
    if occasion:
        conditions.append(Outfit.occasion == occasion)
    if date_from:
        conditions.append(Outfit.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        conditions.append(Outfit.created_at <= datetime.combine(date_to, datetime.max.time()))

    where = and_(*conditions)
    total = (await db.execute(select(func.count()).select_from(Outfit).where(where))).scalar_one()
    rows = list(
        (
            await db.execute(
                select(Outfit)
                .where(where)
                .order_by(Outfit.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return OutfitListResponse(
        outfits=[await build_outfit_response(db, outfit) for outfit in rows],
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )


@router.get("/pending", response_model=OutfitListResponse)
async def pending_outfits(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
    limit: int = Query(3, ge=1, le=20),
) -> OutfitListResponse:
    return await list_outfits(db, user, page=1, page_size=limit, status_filter="pending")


@router.get("/{outfit_id}", response_model=OutfitResponse)
async def get_outfit(
    outfit_id: str, db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser
) -> OutfitResponse:
    return await build_outfit_response(db, await _owned_outfit(db, user.id, outfit_id))


async def _set_status(
    db: AsyncSession, outfit: Outfit, new_status: str, *, count_wear: bool
) -> Outfit:
    outfit.status = new_status
    outfit.updated_at = datetime.now(UTC)
    if count_wear:
        today = date.today()
        items = list(
            (
                await db.execute(
                    select(ClothingItem)
                    .join(OutfitItem, OutfitItem.item_id == ClothingItem.id)
                    .where(OutfitItem.outfit_id == outfit.id)
                )
            )
            .scalars()
            .all()
        )
        for item in items:
            item.wear_count = (item.wear_count or 0) + 1
            item.last_worn_at = today
            item.acceptance_count = (item.acceptance_count or 0) + 1
    await db.flush()
    return outfit


@router.post("/{outfit_id}/accept", response_model=OutfitResponse)
async def accept_outfit(
    outfit_id: str, db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser
) -> OutfitResponse:
    outfit = await _owned_outfit(db, user.id, outfit_id)
    outfit = await _set_status(db, outfit, "accepted", count_wear=True)
    return await build_outfit_response(db, outfit)


@router.post("/{outfit_id}/reject", response_model=OutfitResponse)
async def reject_outfit(
    outfit_id: str, db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser
) -> OutfitResponse:
    outfit = await _owned_outfit(db, user.id, outfit_id)
    outfit = await _set_status(db, outfit, "rejected", count_wear=False)
    return await build_outfit_response(db, outfit)


@router.post("/{outfit_id}/skip", response_model=OutfitResponse)
async def skip_outfit(
    outfit_id: str, db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser
) -> OutfitResponse:
    outfit = await _owned_outfit(db, user.id, outfit_id)
    outfit = await _set_status(db, outfit, "skipped", count_wear=False)
    return await build_outfit_response(db, outfit)


@router.post("/{outfit_id}/feedback", response_model=OutfitResponse)
async def feedback(
    outfit_id: str,
    payload: FeedbackRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
) -> OutfitResponse:
    outfit = await _owned_outfit(db, user.id, outfit_id)
    outfit.feedback = {
        "rating": payload.rating,
        "comment": payload.comment,
        "worn_at": date.today().isoformat() if (payload.worn or payload.actually_worn) else None,
    }
    if payload.worn or payload.actually_worn:
        await _set_status(db, outfit, "accepted", count_wear=True)
    else:
        await db.flush()
    return await build_outfit_response(db, outfit)


@router.delete("/{outfit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_outfit(
    outfit_id: str, db: Annotated[AsyncSession, Depends(get_db)], user: CurrentUser
) -> None:
    await db.delete(await _owned_outfit(db, user.id, outfit_id))
    await db.flush()
