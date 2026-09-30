"""Response/request models.

Field names mirror the original ``ItemResponse`` / ``OutfitResponse`` shapes
that the Next.js frontend already consumes, because the trial reuses that UI.
Fields for removed subsystems (family ratings, wash tracking, studio, pairings)
are either absent or defaulted, and the frontend code paths that touched them
were deleted rather than left pointing at nothing.
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    type: str
    subtype: str | None = None
    name: str | None = None
    brand: str | None = None
    notes: str | None = None
    favorite: bool = False

    # URL fields are what the UI renders; *_key are the storage keys.
    image_path: str = ""
    thumbnail_path: str | None = None
    medium_path: str | None = None
    original_image_path: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None
    medium_url: str | None = None

    tags: dict[str, Any] = Field(default_factory=dict)
    colors: list[str] = Field(default_factory=list)
    primary_color: str | None = None
    pattern: str | None = None
    material: str | None = None
    formality: str | None = None
    fit: str | None = None
    style: list[str] = Field(default_factory=list)
    season: list[str] = Field(default_factory=list)

    status: Literal["processing", "ready", "error", "archived"] = "ready"
    ai_processed: bool = False
    ai_confidence: float | None = None
    ai_description: str | None = None
    ai_error: str | None = None
    ai_unrecognized_type: str | None = None
    ai_started_at: datetime | None = None
    ai_completed_at: datetime | None = None
    processing_kind: Literal["tagging", "background_removal", "rotate"] | None = None
    tagging_status: Literal["pending", "tagged"] = "tagged"
    tagged_by: Literal["auto", "manual"] | None = None
    tagged_at: datetime | None = None

    wear_count: int = 0
    last_worn_at: date | None = None
    last_suggested_at: date | None = None
    suggestion_count: int = 0
    acceptance_count: int = 0

    # Kept so the existing UI renders without conditionals; the trial never
    # changes them (wash tracking was removed as a feature, not stubbed with
    # a button that calls a missing endpoint).
    wears_since_wash: int = 0
    last_washed_at: date | None = None
    wash_interval: int | None = None
    needs_wash: bool = False
    effective_wash_interval: int | None = None
    additional_images: list[dict[str, Any]] = Field(default_factory=list)

    is_archived: bool = False
    archived_at: datetime | None = None
    archive_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ItemListResponse(BaseModel):
    items: list[ItemResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=100)
    brand: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)
    type: str | None = Field(default=None, max_length=50)
    subtype: str | None = Field(default=None, max_length=50)
    primary_color: str | None = Field(default=None, max_length=50)
    colors: list[str] | None = None
    pattern: str | None = Field(default=None, max_length=50)
    material: str | None = Field(default=None, max_length=50)
    formality: str | None = Field(default=None, max_length=50)
    fit: str | None = Field(default=None, max_length=50)
    style: list[str] | None = None
    season: list[str] | None = None
    favorite: bool | None = None
    is_archived: bool | None = None
    status: Literal["ready", "error", "archived"] | None = None


class BulkUploadResult(BaseModel):
    filename: str
    success: bool
    item_id: str | None = None
    error: str | None = None
    type: str | None = None


class BulkUploadResponse(BaseModel):
    total: int
    successful: int
    failed: int
    results: list[BulkUploadResult]


class TaggingProgress(BaseModel):
    """Static snapshot. With synchronous processing nothing is ever queued,
    so these numbers come straight from the items table."""

    processing: int = 0
    queued: int = 0
    analyzing: int = 0
    failed: int = 0
    completed: int = 0
    total: int = 0
    batch_total: int = 0
    batch_completed: int = 0
    batch_failed: int = 0
    current: list[dict[str, Any]] = Field(default_factory=list)
    recent: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)


class OutfitItemResponse(BaseModel):
    id: str
    type: str
    subtype: str | None = None
    name: str | None = None
    primary_color: str | None = None
    colors: list[str] = Field(default_factory=list)
    image_path: str = ""
    thumbnail_path: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None
    layer_type: str | None = None
    position: int = 0


class OutfitResponse(BaseModel):
    id: str
    occasion: str
    scheduled_for: date | None = None
    status: str
    source: str = "on_demand"
    name: str | None = None
    headline: str | None = None
    reasoning: str | None = None
    style_notes: str | None = None
    highlights: list[str] | None = None
    season: str | None = None
    formality: str | None = None
    palette: list[str] | None = None
    notes: str | None = None
    weather: dict[str, Any] | None = None
    items: list[OutfitItemResponse] = Field(default_factory=list)
    feedback: dict[str, Any] | None = None
    is_starter_suggestion: bool = False
    created_at: datetime


class OutfitListResponse(BaseModel):
    outfits: list[OutfitResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class WeatherOverride(BaseModel):
    model_config = ConfigDict(extra="ignore")

    temperature: float
    feels_like: float | None = None
    humidity: float = 50
    precipitation_chance: float = 0
    condition: str = "clear"


class SuggestRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    occasion: str = "casual"
    time_of_day: str | None = None
    weather_override: WeatherOverride | None = None
    include_items: list[str] = Field(default_factory=list)
    exclude_items: list[str] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)
    worn: bool | None = None
    actually_worn: bool | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str
    avatar_url: str | None = None
    locale: str = "en"
    default_occasion: str | None = None
    temperature_unit: str = "celsius"
    onboarding_completed: bool = True
    location_lat: float | None = None
    location_lon: float | None = None
    location_name: str | None = None


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str | None = Field(default=None, max_length=100)
    default_occasion: str | None = Field(default=None, max_length=50)
    temperature_unit: Literal["celsius", "fahrenheit"] | None = None
    locale: str | None = Field(default=None, max_length=10)
    location_lat: float | None = None
    location_lon: float | None = None
    location_name: str | None = Field(default=None, max_length=200)
    onboarding_completed: bool | None = None


class AuthSyncRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    external_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    id_token: str | None = None
    password: str | None = None


class AuthSyncResponse(BaseModel):
    id: str
    access_token: str
    token_type: str = "bearer"
    is_new_user: bool = False
    onboarding_completed: bool = True


class AuthStatusResponse(BaseModel):
    auth_required: bool
    mode: str
    oidc_enabled: bool = False
    demo_password_enabled: bool = False
    ai_configured: bool = False
    storage: str = "local-ephemeral"


class LocationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    latitude: float | None = None
    longitude: float | None = None
    location: str | None = None
