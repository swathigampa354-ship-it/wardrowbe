"""Wardrowbe trial API.

FastAPI on its own is not the deployment unit: the Dockerfile ships this
alongside the Next.js UI in one container, and the UI's ``/api/v1`` proxy
points at it. Run standalone with:

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api.items import router as items_router
from app.api.misc import images_router
from app.api.misc import router as misc_router
from app.api.outfits import router as outfits_router
from app.config import get_settings
from app.database import dispose_engine, init_db
from app.deps import requeue_orphaned_analysis


def _settings():
    """Accessed, not snapshotted: a module-level copy would freeze the env at import."""
    return get_settings()


logging.basicConfig(
    level=logging.DEBUG if _settings().debug else logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("wardrowbe.trial")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    # Analysis is a background task inside this process now, so a restart mid-flight
    # leaves rows stuck on "processing" forever. Fail them loudly instead: the UI
    # then offers Re-analyze rather than spinning at a queue that no longer exists.
    try:
        n = await requeue_orphaned_analysis()
        if n:
            logger.warning("Reset %d item(s) left 'processing' by a previous shutdown", n)
    except Exception:  # pragma: no cover - never block startup on cleanup
        logger.exception("Could not reset orphaned 'processing' rows")
    logger.info(
        "Auth: %s", "token required" if _settings().require_auth else "none (single demo user)"
    )
    if _settings().ai_configured:
        logger.info(
            "AI: %s vision=%s text=%s",
            _settings().ai_base_url,
            _settings().ai_vision_model,
            _settings().ai_text_model,
        )
    else:
        logger.warning(
            "AI: not configured - uploads will save without tags and suggestions will 503"
        )
    if _settings().s3_enabled:
        logger.info("Storage: S3 bucket %s (persistent)", _settings().storage_s3_bucket)
    else:
        logger.warning(
            "Storage: local disk %s is EPHEMERAL on Render Free - photos are lost on "
            "redeploy/restart/spin-down. Set STORAGE_S3_* for durable images.",
            _settings().storage_dir,
        )
    yield
    await dispose_engine()


app = FastAPI(
    title=f"{_settings().app_name} API",
    description="AI wardrobe analysis and outfit generation - trial build (no Redis, no workers).",
    version="trial-1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _settings().debug else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _settings().debug else None,
)

if _settings().cors_origins and _settings().cors_origins != ["*"]:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings().cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

app.add_middleware(GZipMiddleware, minimum_size=500)

_db_ready = False


@app.middleware("http")
async def ensure_schema(request, call_next):
    """create_all in middleware, not just in lifespan: some PaaS hosts (and
    ``python -c 'uvicorn...'`` wrappers) delay or skip lifespan startup, and an
    empty schema would otherwise surface as a confusing 500 on first upload."""
    global _db_ready
    if not _db_ready:
        await init_db()
        _db_ready = True
    return await call_next(request)


app.include_router(misc_router, prefix="/api/v1")
app.include_router(items_router, prefix="/api/v1")
app.include_router(outfits_router, prefix="/api/v1")
app.include_router(images_router, prefix="/api/v1")


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "The request was rejected: "
            + "; ".join(
                f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
            )
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "The server hit an unexpected error. The item was not lost - try again."
        },
    )


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    """Container health target (the /api/v1 router lives under the API prefix)."""
    return {"status": "ok"}
