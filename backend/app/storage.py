"""Image persistence.

Two backends, one interface:

* ``LocalBlobStore`` — files under ``STORAGE_DIR``. Simple, and the default.
  **Not durable on Render Free**: the filesystem is ephemeral and is wiped on
  redeploy/restart/spin-down. The app reports that honestly on
  ``GET /api/v1/health/storage`` and in the README.
* ``S3BlobStore`` — any S3-compatible bucket (Cloudflare R2, Backblaze B2,
  MinIO, AWS S3). Optional: install the ``s3`` extra and set the STORAGE_S3_*
  variables. Images are cached in memory after first read because free-tier
  egress on the app host is metered.

Either way the frontend only ever sees ``/api/v1/images/{key}?size=...``, so
switching backends does not change stored URLs.
"""

import asyncio
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Protocol

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def _settings():
    """Accessed, not snapshotted: a module-level copy would freeze the env at import."""
    return get_settings()


_IMAGE_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
# Read cache for the S3 backend: keeps repeat gallery loads off the network.
_CACHE_MAX_BYTES = 64 * 1024 * 1024
_cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
_cache_bytes = 0


class BlobStore(Protocol):
    persistent: bool

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> tuple[bytes, str] | None: ...
    async def delete(self, keys: list[str]) -> None: ...


def _validate_key(key: str) -> str:
    clean = key.lstrip("/")
    if ".." in Path(clean).parts or not clean:
        raise ValueError(f"Unsafe storage key: {key!r}")
    return clean


def _cache_put(key: str, data: bytes, content_type: str) -> None:
    global _cache_bytes
    _cache[key] = (data, content_type)
    _cache_bytes += len(data)
    _cache.move_to_end(key)
    while _cache_bytes > _CACHE_MAX_BYTES and len(_cache) > 1:
        _, (evicted, _) = _cache.popitem(last=False)
        _cache_bytes -= len(evicted)


class LocalBlobStore:
    persistent = False

    def __init__(self, root: Path | None = None) -> None:
        # Resolved per instance, so a STORAGE_DIR change (tests, or a host that
        # remaps the writeable path) is honoured rather than frozen at import.
        self.root = (root or Path(_settings().storage_dir)).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _validate_key(key)).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError("Storage key escapes the storage root")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> tuple[bytes, str] | None:
        path = self._path(key)
        if not path.exists():
            return None
        data = await asyncio.to_thread(path.read_bytes)
        return data, _IMAGE_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")

    async def delete(self, keys: list[str]) -> None:
        for key in keys:
            path = self._path(key)
            if path.exists():
                await asyncio.to_thread(path.unlink, True)


class S3BlobStore:
    persistent = True

    def __init__(self) -> None:
        # Imported lazily so the default trial install does not need boto3.
        import boto3  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415

        self.bucket = _settings().storage_s3_bucket or ""
        self._s3 = boto3.client(
            "s3",
            endpoint_url=_settings().storage_s3_endpoint,
            aws_access_key_id=_settings().storage_s3_access_key,
            aws_secret_access_key=_settings().storage_s3_secret_key,
            region_name=_settings().storage_s3_region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 2}),
        )

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        key = _validate_key(key)
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ACL="public-read",
        )
        _cache_put(key, data, content_type)

    async def get(self, key: str) -> tuple[bytes, str] | None:
        key = _validate_key(key)
        cached = _cache.get(key)
        if cached:
            _cache.move_to_end(key)
            return cached

        def _fetch() -> tuple[bytes, str] | None:
            try:
                obj = self._s3.get_object(Bucket=self.bucket, Key=key)
            except Exception as exc:  # noqa: BLE001 - surfaced as a 404 upstream
                logger.warning("S3 read failed for %s: %s", key, exc)
                return None
            body = obj["Body"].read()
            return body, obj.get("ContentType") or _IMAGE_CONTENT_TYPES.get(
                Path(key).suffix.lower(), "image/jpeg"
            )

        result = await asyncio.to_thread(_fetch)
        if result:
            _cache_put(key, result[0], result[1])
        return result

    async def delete(self, keys: list[str]) -> None:
        valid = [_validate_key(k) for k in keys]
        if not valid:
            return

        def _delete() -> None:
            for key in valid:
                try:
                    self._s3.delete_object(Bucket=self.bucket, Key=key)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("S3 delete failed for %s: %s", key, exc)
                _cache.pop(key, None)

        await asyncio.to_thread(_delete)


_store: BlobStore | None = None
_store_root: str | None = None


def reset_store() -> None:
    """Drop the cached backend (used by tests and on config change)."""
    global _store, _store_root
    _store = None
    _store_root = None


def get_store() -> BlobStore:
    """Cached backend, invalidated if STORAGE_DIR moves underneath us."""
    global _store, _store_root
    if (
        _store is not None
        and not _settings().s3_enabled
        and _store_root != str(_settings().storage_dir)
    ):
        reset_store()
    if _store is None:
        if _settings().s3_enabled:
            try:
                _store = S3BlobStore()
                logger.info("Image storage: S3 bucket %s", _settings().storage_s3_bucket)
            except ImportError:
                logger.error(
                    "STORAGE_S3_* is configured but boto3 is missing. "
                    "Install the 's3' extra; falling back to the ephemeral local disk."
                )
                _store = LocalBlobStore()
        else:
            _store = LocalBlobStore()
            # Record the root this instance was built for, otherwise the check above always
            # sees None != storage_dir, and every image read re-invents the store (mkdir +
            # resolve + a repeated EPHEMERAL warning per request).
            _store_root = str(_settings().storage_dir)
            logger.warning(
                "Image storage: local disk (%s) — EPHEMERAL on Render Free. "
                "Uploaded photos are lost on redeploy, restart, or spin-down.",
                _settings().storage_dir,
            )
    return _store


def image_url(key: str | None, size: str = "original") -> str | None:
    if not key:
        return None
    return f"/api/v1/images/{_validate_key(key)}?size={size}"


async def probe_connectivity() -> str:
    """Used by /health/storage. 'ok' | 'unreachable' | 'not-configured'."""
    if not _settings().s3_enabled:
        return "not-configured"
    store = get_store()
    if not isinstance(store, S3BlobStore):
        return "not-configured"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.get(f"{_settings().storage_s3_endpoint.rstrip('/')}/")
        return "ok"
    except Exception:  # noqa: BLE001 - health probe must never raise
        return "unreachable"
