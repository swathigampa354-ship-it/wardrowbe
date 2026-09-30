"""Auth, image serving, weather and health endpoints in one small router.

These four were separate modules upstream (auth.py 199, images.py 97,
weather.py 161, health.py 97 lines). The trial has one user and no OIDC, so
they fit here.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import DEMO_USER_EMAIL, DEMO_USER_ID, CurrentUser, check_demo_password, issue_token
from app.config import get_settings
from app.database import get_db
from app.models import User
from app.schemas import (
    AuthStatusResponse,
    AuthSyncRequest,
    AuthSyncResponse,
    LocationRequest,
    UserResponse,
    UserUpdate,
)
from app.storage import get_store, probe_connectivity
from app.weather import current_weather, fetch_weather, geocode

logger = logging.getLogger(__name__)
def _settings():
    """Accessed, not snapshotted: a module-level copy would freeze the env at import."""
    return get_settings()

router = APIRouter()
images_router = APIRouter(prefix="/images", tags=["images"])


# ---------------------------------------------------------------- auth -----
@router.get("/auth/status", response_model=AuthStatusResponse, tags=["auth"])
async def auth_status() -> AuthStatusResponse:
    return AuthStatusResponse(
        auth_required=_settings().require_auth,
        mode="none" if not _settings().require_auth else "demo-token",
        oidc_enabled=False,
        demo_password_enabled=bool(_settings().demo_password),
        ai_configured=_settings().ai_configured,
        storage="s3" if _settings().s3_enabled else "local-ephemeral",
    )


@router.post("/auth/sync", response_model=AuthSyncResponse, tags=["auth"])
async def auth_sync(
    payload: AuthSyncRequest, db: Annotated[AsyncSession, Depends(get_db)]
) -> AuthSyncResponse:
    """Kept because the existing Next.js/NextAuth flow calls it on sign-in.

    OIDC validation is gone: in the trial this creates-or-returns the single
    demo user and hands back a long-lived JWT.
    """
    check_demo_password(payload.password)

    user = await db.get(User, DEMO_USER_ID)
    is_new = user is None
    if user is None:
        user = User(
            id=DEMO_USER_ID,
            email=payload.email or DEMO_USER_EMAIL,
            display_name=payload.display_name or "Demo User",
            avatar_url=payload.avatar_url,
        )
        db.add(user)
        await db.flush()
    return AuthSyncResponse(
        id=user.id, access_token=issue_token(user.id), is_new_user=is_new, onboarding_completed=True
    )


@router.get("/users/me", response_model=UserResponse, tags=["users"])
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.patch("/users/me", response_model=UserResponse, tags=["users"])
async def update_me(
    payload: UserUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    del db
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(user, key, value)
    await db.flush()
    return UserResponse.model_validate(user)


@router.post("/users/me/location", response_model=UserResponse, tags=["users"])
async def set_location(
    payload: LocationRequest, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> UserResponse:
    """Browser geolocation is the only source kept; the IP-geolocation fallback
    was removed because it sends visitors' IPs to a third party."""
    if payload.latitude is not None and payload.longitude is not None:
        user.location_lat, user.location_lon = payload.latitude, payload.longitude
        user.location_name = payload.location or f"{payload.latitude:.2f},{payload.longitude:.2f}"
    elif payload.location:
        coords = await geocode(payload.location)
        if coords is None:
            raise HTTPException(
                status_code=400, detail=f"Couldn't find that location: {payload.location}"
            )
        user.location_lat, user.location_lon = coords
        user.location_name = payload.location
    else:
        raise HTTPException(status_code=400, detail="Provide latitude+longitude or a location name")
    await db.flush()
    return UserResponse.model_validate(user)


@router.post("/users/me/onboarding/complete", response_model=UserResponse, tags=["users"])
async def complete_onboarding(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]) -> UserResponse:
    await db.flush()
    return UserResponse.model_validate(user)


# ------------------------------------------------------------- weather -----
@router.get("/weather/current", tags=["weather"])
async def weather_current(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    if not _settings().weather_enabled:
        raise HTTPException(status_code=503, detail="Weather is disabled on this instance (WEATHER_ENABLED=false).")
    payload = await current_weather(db, user.id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail="No location set. Set one in Settings, or configure DEFAULT_LATITUDE/DEFAULT_LONGITUDE.",
        )
    return payload


@router.post("/weather/preview", tags=["weather"])
async def weather_preview(
    payload: LocationRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    lat, lon = payload.latitude, payload.longitude
    if lat is None or lon is None:
        if payload.location:
            coords = await geocode(payload.location)
            if coords is None:
                raise HTTPException(status_code=404, detail="Location not found")
            lat, lon = coords
        else:
            coords = await current_weather(db, user.id)
            if coords is None:
                raise HTTPException(status_code=404, detail="No location set")
            return coords
    weather = await fetch_weather(lat, lon)
    if weather is None:
        raise HTTPException(status_code=502, detail="Open-Meteo did not answer. Try again shortly.")
    return weather


# --------------------------------------------------------------- images ----
@images_router.get("/{key:path}")
async def get_image(key: str, size: str = "original") -> Response:
    """Serve an image from whichever backend is configured.

    The frontend only ever stores/renders these relative URLs, which is what
    makes local-disk and S3 interchangeable without rewriting rows.
    """
    store = get_store()

    wanted = key
    if size == "thumbnail" and "_thumb" not in key:
        wanted = key.rsplit(".", 1)[0] + "_thumb.jpg"
    elif size == "medium" and "_medium" not in key:
        wanted = key.rsplit(".", 1)[0] + "_medium.jpg"

    stored = await store.get(wanted)
    if stored is None and wanted != key:
        stored = await store.get(key)
    if stored is None:
        raise HTTPException(status_code=404, detail="Image not found in storage")

    data, content_type = stored
    # Uploaded originals rarely change, so cache them in the browser: a
    # spin-up of a free instance should not refetch every gallery tile.
    max_age = "604800" if store.persistent else "86400"
    return Response(
        content=data, media_type=content_type, headers={"Cache-Control": f"public, max-age={max_age}"}
    )


@images_router.get("/redirect/{key:path}")
async def redirect_image(key: str) -> RedirectResponse:
    return RedirectResponse(url=f"/api/v1/images/{key}")


# --------------------------------------------------------------- health ----
@router.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "healthy", "service": _settings().app_name}


@router.get("/health/ready", tags=["health"])
async def health_ready(db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    from sqlalchemy import text

    checks: dict[str, str] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"unhealthy: {exc}"
    overall = "healthy" if all(v == "healthy" for v in checks.values()) else "unhealthy"
    return {"status": overall, "checks": checks}


@router.get("/health/features", tags=["health"])
async def health_features() -> dict:
    """What the UI reads to decide whether to render optional controls.
    background_removal is False here because the feature was removed, not stubbed."""
    return {"background_removal": False}


@router.get("/health/storage", tags=["health"])
async def health_storage() -> dict:
    if _settings().s3_enabled:
        return {
            "backend": "s3",
            "bucket": _settings().storage_s3_bucket,
            "persistent": True,
            "connectivity": await probe_connectivity(),
        }
    return {
        "backend": "local",
        "path": _settings().storage_dir,
        "persistent": False,
        "note": "Local disk on Render Free is ephemeral: uploads are lost on redeploy, restart, or spin-down.",
    }


@router.get("/capabilities", tags=["health"])
async def capabilities() -> dict:
    from app.ai_service import get_ai

    ai_health = await get_ai().check_health()
    return {
        "ai": {
            "vision": _settings().ai_configured,
            "text": _settings().ai_configured,
            "provider": _settings().ai_base_url or None,
            "vision_models": _settings().ai_vision_models,
            "text_models": _settings().ai_text_models,
            "reachability": ai_health,
        },
        "features": {
            "background_removal": False,
            "weather": _settings().weather_enabled,
            "family": False,
            "learning": False,
            "notifications": False,
            "studio": False,
            "pairings": False,
            "async_queue": False,
        },
        "auth": {"required": _settings().require_auth},
        "storage": {"s3": _settings().s3_enabled, "persistent": _settings().s3_enabled},
        "version": "trial-1.0.0",
    }
