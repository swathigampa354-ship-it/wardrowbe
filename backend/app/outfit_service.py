"""Outfit generation, in one request, with no queue in the middle.

Ported from the original ``recommendation_service.py`` (1000 lines) plus
``item_scorer.py`` (460 lines), reduced to what a single-user trial needs:

  kept    — role-aware candidate selection, a compact scoring pass that
            reflects weather/formality/season/recency, the numbered-item prompt
            format, index->UUID mapping, and tolerant response parsing.
  dropped — learned-preference injection, pairing bonuses from the pairings
            table, mandatory-item handling, body-measurement logic,
            recently-rejected/suggested de-duplication across history,
            notification scheduling, and the Redis suggestion cache.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_service import AIError, extract_json, get_ai
from app.models import ClothingItem, Outfit, OutfitItem, layer_of
from app.prompts import OUTFIT_PROMPT

logger = logging.getLogger(__name__)

TOP_LAYERS = {"top"}
BOTTOM_LAYERS = {"bottom"}
FULL_BODY = {"dress"}
SHOE_LAYERS = {"shoes"}
OUTER_LAYERS = {"layer"}
ACCESSORY_LAYERS = {"accessory"}

HEAVY_MATERIALS = {"wool", "knit", "fleece", "cashmere", "down", "sherpa", "tweed", "corduroy"}
LIGHT_MATERIALS = {"linen", "cotton", "chambray", "seersucker", "nylon", "canvas"}

MAX_CANDIDATES = 40


class NotEnoughItemsError(ValueError):
    """Raised when the wardrobe cannot possibly form an outfit."""


@dataclass
class WeatherLike:
    temperature: float = 20.0
    feels_like: float = 20.0
    condition: str = "clear"
    precipitation_chance: float = 0.0

    @property
    def is_cold(self) -> bool:
        return self.feels_like < 12

    @property
    def is_hot(self) -> bool:
        return self.feels_like >= 27

    @property
    def is_wet(self) -> bool:
        return self.precipitation_chance >= 40 or "rain" in self.condition.lower()

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "WeatherLike":
        if not payload:
            return cls()
        return cls(
            temperature=float(payload.get("temperature", 20)),
            feels_like=float(payload.get("feels_like", payload.get("temperature", 20))),
            condition=str(payload.get("condition", "clear")),
            precipitation_chance=float(payload.get("precipitation_chance", 0)),
        )


def season_for(day: date | None = None, latitude: float | None = None) -> str:
    """Northern-hemisphere default; flipped south of the equator."""
    d = day or datetime.now(UTC).date()
    month = d.month
    northern = (
        3 <= month <= 5
        and "spring"
        or 6 <= month <= 8
        and "summer"
        or 9 <= month <= 11
        and "fall"
        or "winter"
    )
    if latitude is not None and latitude < 0:
        flip = {"spring": "fall", "summer": "winter", "fall": "spring", "winter": "summer"}
        return flip[northern]
    return northern


@dataclass
class ScoredItem:
    item: ClothingItem
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


def _weather_score(item: ClothingItem, weather: WeatherLike) -> float:
    score = 0.0
    heavy = item.type in {"coat", "sweater", "hoodie", "cardigan", "boots", "knit", "jeans"} or (
        item.material in HEAVY_MATERIALS
    )
    light = item.type in {"tank-top", "shorts", "sandals", "t-shirt", "linen"} or (
        item.material in LIGHT_MATERIALS
    )
    if weather.is_cold:
        if heavy:
            score += 2.0
        if light and layer_of(item.type) in TOP_LAYERS | BOTTOM_LAYERS:
            score -= 1.5
    elif weather.is_hot:
        if light:
            score += 2.0
        if heavy:
            score -= 2.5
    if weather.is_wet and item.type in {"suede", "boots", "sandals"}:
        score += 0.5 if item.type == "boots" else -0.5
    return score


def _formality_score(item: ClothingItem, occasion: str) -> float:
    ladder = ["very-casual", "casual", "smart-casual", "business-casual", "formal"]
    want = {
        "casual": "casual",
        "everyday": "casual",
        "weekend": "casual",
        "athleisure": "very-casual",
        "gym": "very-casual",
        "travel": "casual",
        "date": "smart-casual",
        "dinner": "smart-casual",
        "night-out": "smart-casual",
        "party": "smart-casual",
        "smart-casual": "smart-casual",
        "office": "business-casual",
        "work": "business-casual",
        "business": "business-casual",
        "interview": "formal",
        "wedding": "formal",
        "formal": "formal",
    }.get(occasion.lower(), "smart-casual")
    if not item.formality:
        return 0.0
    try:
        gap = abs(ladder.index(item.formality) - ladder.index(want))
    except ValueError:
        return 0.0
    return {0: 2.0, 1: 0.8, 2: -1.0}.get(gap, -2.0)


def _season_score(item: ClothingItem, season: str) -> float:
    if not item.season:
        return 0.0
    if "all-season" in item.season:
        return 0.6
    return 1.2 if season in item.season else -0.8


def _recency_score(item: ClothingItem, today: date) -> float:
    if item.last_worn_at is None:
        return 1.0
    days = (today - item.last_worn_at).days
    if days <= 2:
        return -1.5
    if days <= 7:
        return -0.3
    if days >= 21:
        return 0.6
    return 0.0


def score_items(
    items: list[ClothingItem], weather: WeatherLike, occasion: str, season: str, today: date
) -> list[ScoredItem]:
    scored: list[ScoredItem] = []
    for item in items:
        s = 0.0
        s += _weather_score(item, weather)
        s += _formality_score(item, occasion)
        s += _season_score(item, season)
        s += _recency_score(item, today)
        if item.favorite:
            s += 0.7
        if item.ai_processed:
            s += 0.3
        scored.append(ScoredItem(item=item, score=s))
    return scored


def select_candidates(scored: list[ScoredItem]) -> list[ScoredItem]:
    """Keep the strongest piece per role so a big wardrobe still fits the prompt."""
    by_layer: dict[str, list[ScoredItem]] = defaultdict(list)
    for entry in scored:
        layer = layer_of(entry.item.type) or "other"
        by_layer[layer].append(entry)

    quotas = {
        "top": 12,
        "bottom": 10,
        "dress": 8,
        "shoes": 8,
        "layer": 8,
        "accessory": 8,
        "other": 4,
    }
    chosen: list[ScoredItem] = []
    for layer, quota in quotas.items():
        pool = sorted(by_layer.get(layer, []), key=lambda e: e.score, reverse=True)[:quota]
        chosen.extend(pool)

    chosen.sort(key=lambda e: e.score, reverse=True)
    return chosen[:MAX_CANDIDATES]


def format_items(candidates: list[ScoredItem], today: date) -> tuple[str, dict[int, str]]:
    lines: list[str] = []
    number_map: dict[int, str] = {}
    for number, entry in enumerate(candidates, start=1):
        item = entry.item
        number_map[number] = item.id
        parts: list[str] = []
        if item.name:
            parts.append(f'"{item.name}"')
        parts.append(f"{item.subtype} ({item.type})" if item.subtype else item.type)
        colors = item.colors or ([item.primary_color] if item.primary_color else [])
        if colors:
            parts.append("colors: " + ", ".join(colors[:3]))
        elif item.primary_color:
            parts.append(item.primary_color)
        if item.pattern and item.pattern != "solid":
            parts.append(item.pattern)
        if item.material:
            parts.append(item.material)
        if item.formality:
            parts.append(item.formality)
        if item.style:
            parts.append("style: " + ", ".join(item.style[:2]))
        if item.season:
            parts.append("season: " + ", ".join(item.season[:2]))
        if item.fit:
            parts.append(f"{item.fit} fit")
        if item.last_worn_at:
            days = (today - item.last_worn_at).days
            parts.append(f"worn {days} days ago" if 0 <= days <= 14 else "not worn recently")
        else:
            parts.append("never worn")
        lines.append(f"[{number}] " + " | ".join(parts))
    return "\n".join(lines), number_map


def _parse_outfits(content: str, number_map: dict[int, str]) -> list[dict[str, Any]]:
    payload = extract_json(content)

    raw_outfits: list[Any] = []
    if isinstance(payload, dict) and isinstance(payload.get("outfits"), list):
        raw_outfits = payload["outfits"]
    elif isinstance(payload, list):
        raw_outfits = payload
    elif isinstance(payload, dict):
        raw_outfits = [payload]

    outfits: list[dict[str, Any]] = []
    seen_key: set[tuple[str, ...]] = set()
    for entry in raw_outfits:
        if not isinstance(entry, dict):
            continue
        ids: list[str] = []
        for ref in entry.get("items") or []:
            number = None
            if isinstance(ref, bool):
                continue
            if isinstance(ref, int):
                number = ref
            elif isinstance(ref, str):
                digits = "".join(ch for ch in ref if ch.isdigit())
                number = int(digits) if digits else None
            item_id = number_map.get(number) if number is not None else None
            if item_id and item_id not in ids:
                ids.append(item_id)
        if not ids:
            continue
        key = tuple(sorted(ids))
        if key in seen_key:
            continue
        seen_key.add(key)

        highlights = entry.get("highlights")
        if isinstance(highlights, str):
            highlights = [highlights]
        if isinstance(highlights, list):
            highlights = [str(h)[:300] for h in highlights if str(h).strip()][:5]
        else:
            highlights = []

        outfits.append(
            {
                "item_ids": ids,
                "headline": str(entry.get("headline") or "").strip()[:100] or None,
                "reasoning": str(entry.get("why") or "").strip()[:1000] or None,
                "style_notes": str(entry.get("styling_tip") or "").strip()[:600] or None,
                "highlights": highlights,
            }
        )
    if not outfits:
        raise AIError("The AI didn't return any usable outfits (no valid item numbers). Try again.")
    return outfits[:3]


async def generate_outfits(
    db: AsyncSession,
    *,
    user_id: str,
    occasion: str,
    weather_payload: dict[str, Any] | None = None,
    include_items: list[str] | None = None,
    exclude_items: list[str] | None = None,
    count: int = 3,
) -> list[Outfit]:
    weather = WeatherLike.from_payload(weather_payload)
    today = datetime.now(UTC).date()
    season = season_for(today)

    stmt = select(ClothingItem).where(
        ClothingItem.user_id == user_id,
        ClothingItem.is_archived.is_(False),
        ClothingItem.status != "error",
    )
    if exclude_items:
        stmt = stmt.where(ClothingItem.id.notin_(exclude_items))
    items = list((await db.execute(stmt)).scalars().all())

    if not items:
        raise NotEnoughItemsError(
            "Your wardrobe is empty. Upload at least one top, one bottom "
            "and one pair of shoes first."
        )

    by_layer = defaultdict(list)
    for item in items:
        by_layer[layer_of(item.type)].append(item)
    has_dress = bool(by_layer["dress"])
    if not has_dress and not (by_layer["top"] and by_layer["bottom"]):
        missing = ", ".join(
            label
            for label, present in (("a top", by_layer["top"]), ("a bottom", by_layer["bottom"]))
            if not present
        )
        raise NotEnoughItemsError(
            f"Not enough pieces for an outfit yet — you still need {missing} "
            "(or a dress plus shoes). Upload more and try again."
        )
    if not by_layer["shoes"]:
        raise NotEnoughItemsError("Add at least one pair of shoes so the outfit can be completed.")

    scored = score_items(items, weather, occasion, season, today)
    candidates = select_candidates(scored)
    items_text, number_map = format_items(candidates, today)

    weather_text = ""
    if weather_payload:
        weather_text = (
            f"- Weather: {weather.temperature:.0f}°C (feels like {weather.feels_like:.0f}°C), "
            f"{weather.condition}, {weather.precipitation_chance:.0f}% chance of rain\n"
        )
    note_text = (
        "the user asked to include specific pieces — build at least one outfit around them"
        if include_items
        else "no other constraints"
    )

    prompt = OUTFIT_PROMPT.format(
        occasion=occasion,
        season=season,
        weather_text=weather_text,
        items_text=items_text,
        note_text=note_text,
    )

    ai = get_ai()
    try:
        content = await ai.generate_text(
            system="Create complete outfits from a wardrobe.", user=prompt
        )
    except AIError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Outfit generation failed")
        raise AIError(f"Outfit generation failed: {exc}") from exc

    parsed = _parse_outfits(content, number_map)

    # Honour include_items as a soft constraint: if the model ignored it, graft
    # the requested piece into the first outfit rather than failing the request.
    if include_items:
        requested = [i for i in include_items if i in set(number_map.values())]
        if requested and requested[0] not in parsed[0]["item_ids"]:
            parsed[0]["item_ids"] = [requested[0]] + [
                i for i in parsed[0]["item_ids"] if i != requested[0]
            ][:5]

    outfits = parsed[: max(1, min(count, 3))]

    rows: list[Outfit] = []
    for entry in outfits:
        outfit = Outfit(
            user_id=user_id,
            occasion=occasion,
            scheduled_for=today,
            status="pending",
            source="on_demand",
            headline=entry["headline"],
            reasoning=entry["reasoning"],
            style_notes=entry["style_notes"],
            highlights=entry["highlights"],
            season=season,
            weather_data=weather_payload,
            ai_raw_response={"outfit": entry, "model_output": content[:2000]},
            created_at=datetime.now(UTC),
        )
        outfit.items = [
            OutfitItem(item_id=item_id, position=pos)
            for pos, item_id in enumerate(entry["item_ids"])
        ]
        db.add(outfit)
        rows.append(outfit)

    await db.flush()
    touched = {i for entry in outfits for i in entry["item_ids"]}
    if touched:
        await db.execute(
            update(ClothingItem)
            .where(ClothingItem.id.in_(touched))
            .values(
                suggestion_count=func.coalesce(ClothingItem.suggestion_count, 0) + 1,
                last_suggested_at=today,
            )
        )
    return rows
