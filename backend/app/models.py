"""Three tables. Everything else in the original schema belonged to subsystems
the trial removes (families, schedules, notifications, learning, pairings,
studio, wash tracking, item history, additional images).

Portable column types only: UUID/JSONB/ARRAY from
``sqlalchemy.dialects.postgresql`` would pin the trial to PostgreSQL.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    """Minimal user row so items/outfits keep a single owner in a multi-tenant schema."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(100), default="Demo User")
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    locale: Mapped[str] = mapped_column(String(10), default="en")
    default_occasion: Mapped[str | None] = mapped_column(String(50))
    temperature_unit: Mapped[str] = mapped_column(String(10), default="celsius")
    location_lat: Mapped[float | None] = mapped_column(Float)
    location_lon: Mapped[float | None] = mapped_column(Float)
    location_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    items: Mapped[list["ClothingItem"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ClothingItem(Base):
    __tablename__ = "clothing_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Storage keys (relative path on disk, or object key in S3)
    image_key: Mapped[str] = mapped_column(String(500))
    thumbnail_key: Mapped[str | None] = mapped_column(String(500))
    medium_key: Mapped[str | None] = mapped_column(String(500))
    original_key: Mapped[str | None] = mapped_column(String(500))
    # Idempotency key from the frontend upload queue, so a retried upload does
    # not create a second item.
    upload_key: Mapped[str | None] = mapped_column(String(64), index=True)

    type: Mapped[str] = mapped_column(String(50), default="unknown", index=True)
    subtype: Mapped[str | None] = mapped_column(String(50))
    primary_color: Mapped[str | None] = mapped_column(String(50))
    pattern: Mapped[str | None] = mapped_column(String(50))
    material: Mapped[str | None] = mapped_column(String(50))
    formality: Mapped[str | None] = mapped_column(String(50))
    fit: Mapped[str | None] = mapped_column(String(50))
    colors: Mapped[list[str]] = mapped_column(JSON, default=list)
    style: Mapped[list[str]] = mapped_column(JSON, default=list)
    season: Mapped[list[str]] = mapped_column(JSON, default=list)
    tags: Mapped[dict] = mapped_column(JSON, default=dict)

    ai_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_confidence: Mapped[float | None] = mapped_column(Float)
    ai_description: Mapped[str | None] = mapped_column(Text)
    ai_error: Mapped[str | None] = mapped_column(Text)
    ai_unrecognized_type: Mapped[str | None] = mapped_column(String(100))
    ai_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="ready", index=True)

    name: Mapped[str | None] = mapped_column(String(100))
    brand: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)

    wear_count: Mapped[int] = mapped_column(Integer, default=0)
    last_worn_at: Mapped[date | None] = mapped_column(Date)
    last_suggested_at: Mapped[date | None] = mapped_column(Date)
    suggestion_count: Mapped[int] = mapped_column(Integer, default=0)
    acceptance_count: Mapped[int] = mapped_column(Integer, default=0)

    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="items")


class Outfit(Base):
    __tablename__ = "outfits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    occasion: Mapped[str] = mapped_column(String(50), default="casual")
    scheduled_for: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    source: Mapped[str] = mapped_column(String(20), default="on_demand")

    headline: Mapped[str | None] = mapped_column(String(100))
    reasoning: Mapped[str | None] = mapped_column(Text)
    style_notes: Mapped[str | None] = mapped_column(Text)
    highlights: Mapped[list[str] | None] = mapped_column(JSON)
    season: Mapped[str | None] = mapped_column(String(20))
    formality: Mapped[str | None] = mapped_column(String(50))
    palette: Mapped[list[str] | None] = mapped_column(JSON)
    weather_data: Mapped[dict | None] = mapped_column(JSON)
    ai_raw_response: Mapped[dict | None] = mapped_column(JSON)
    feedback: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    items: Mapped[list["OutfitItem"]] = relationship(
        back_populates="outfit", cascade="all, delete-orphan", order_by="OutfitItem.position"
    )


class OutfitItem(Base):
    """Association row; item type/color are joined from ClothingItem at read time."""

    __tablename__ = "outfit_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    outfit_id: Mapped[str] = mapped_column(
        ForeignKey("outfits.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("clothing_items.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    outfit: Mapped[Outfit] = relationship(back_populates="items")
    item: Mapped[ClothingItem] = relationship("ClothingItem")


LAYER_BY_TYPE: dict[str, str] = {
    "shirt": "top",
    "t-shirt": "top",
    "polo": "top",
    "blouse": "top",
    "tank-top": "top",
    "top": "top",
    "sweater": "top",
    "hoodie": "top",
    "cardigan": "top",
    "knit": "top",
    "pants": "bottom",
    "jeans": "bottom",
    "shorts": "bottom",
    "skirt": "bottom",
    "dress": "dress",
    "jumpsuit": "dress",
    "blazer": "layer",
    "jacket": "layer",
    "coat": "layer",
    "vest": "layer",
    "shoes": "shoes",
    "sneakers": "shoes",
    "boots": "shoes",
    "sandals": "shoes",
    "bag": "accessory",
    "belt": "accessory",
    "scarf": "accessory",
    "hat": "accessory",
    "tie": "accessory",
    "socks": "accessory",
    "accessories": "accessory",
}


def layer_of(item_type: str | None) -> str | None:
    return LAYER_BY_TYPE.get((item_type or "").lower())
