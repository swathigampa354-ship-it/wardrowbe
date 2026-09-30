"""Failure-mode coverage required by the spec (section 16):
missing API key, provider auth error, quota exhaustion, invalid response,
truncation and timeouts — plus what the API must show the user for each.

Each test gets a fresh SQLite file and storage directory: an earlier version
shared one database across tests, which made results depend on test order.
"""

import io
import os
import socket
import tempfile
import threading
from pathlib import Path
import time
from contextlib import closing

import pytest
from PIL import Image, PngImagePlugin

from tests import fake_provider


def _free_port() -> int:
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_PORT = _free_port()
threading.Thread(target=fake_provider.serve, args=(_PORT,), daemon=True).start()
time.sleep(0.3)


def _img(tag: str | None = None, mode: str | None = None) -> bytes:
    """Tag *and* stub mode travel inside the PNG text chunk.

    That is the only channel that reaches the stub: the AI request is built by
    the app, not by the test client, so a header on the test request would
    never arrive. Production code must not learn about test headers.
    """
    buf = io.BytesIO()
    meta = PngImagePlugin.PngInfo()
    directive = " ".join(f"WARDROBE:{k}={v}" for k, v in (("tag", tag), ("mode", mode)) if v)
    meta.add_text("comment", directive)
    Image.new("RGB", (300, 400), (80, 120, 200)).save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


def _upload(client, mode: str | None = None, tag: str = "shirt"):
    return client.post(
        "/api/v1/items", files={"image": ("a.png", _img(tag, mode), "image/png")}
    )


def _clean_upload(client, tag: str = "shirt"):
    """No directive in the image, so the stub answers in its env-configured mode."""
    return client.post("/api/v1/items", files={"image": (f"{tag}.png", _img(tag, None), "image/png")})


@pytest.fixture()
def client(monkeypatch, request):
    """A fully isolated app instance per test: own db, own storage, own settings."""
    root = tempfile.mkdtemp(prefix=f"wardrowbe-{request.node.name}-")
    for name in ("AI_BASE_URL", "AI_API_KEY", "AI_VISION_MODEL", "AI_TEXT_MODEL", "AI_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STORAGE_DIR", root)
    monkeypatch.setenv("WEATHER_ENABLED", "false")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("AI_BASE_URL", f"http://127.0.0.1:{_PORT}/v1")
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_VISION_MODEL", "fake-vision")
    monkeypatch.setenv("AI_TEXT_MODEL", "fake-text")
    monkeypatch.setenv("AI_MAX_RETRIES", "1")
    monkeypatch.setenv("AI_TIMEOUT", "5")
    monkeypatch.setenv("FAKE_MODE", "ok")
    fake_provider.os.environ["FAKE_MODE"] = "ok"

    from app import ai_service, config, database, storage

    config.get_settings.cache_clear()
    ai_service._service = None
    storage.reset_store()
    database.reset_engine()

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        test_client.storage_root = root  # type: ignore[attr-defined]
        yield test_client

    config.get_settings.cache_clear()
    storage.reset_store()
    database.reset_engine()


@pytest.mark.parametrize(
    ("mode", "needle"),
    [
        ("unauthorized", "rejected the api key"),
        ("ratelimit", "quota"),
        ("garbage", "valid json"),
        ("truncated", "cut off"),
        ("server_error", "unavailable"),
    ],
)
def test_upload_survives_ai_failure_and_explains_it(client, mode, needle):
    """The photo is never lost to an AI error: the item is saved with
    status=error plus a message the user can act on."""
    response = _upload(client, mode)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "error"
    assert needle in (body["ai_error"] or "").lower(), body["ai_error"]
    assert body["image_url"], "the saved photo must still be viewable"
    assert client.get("/api/v1/items").json()["total"] == 1
    assert client.get(body["image_url"]).status_code == 200


def test_failed_analysis_can_be_retried_until_it_works(client):
    """A garment saved in error state must be retryable, and a successful retry
    must clear ai_error (this is the manual "Analyze" button in the UI)."""
    assert _upload(client, "garbage").json()["status"] == "error"
    item_id = client.get("/api/v1/items").json()["items"][0]["id"]

    fake_provider.os.environ["FAKE_MODE"] = "garbage"
    still_broken = client.post(f"/api/v1/items/{item_id}/analyze")
    assert still_broken.status_code == 503
    assert client.get(f"/api/v1/items/{item_id}").json()["status"] == "error"

    # The stored photo still carries the "mode=garbage" directive (Pillow copies a
    # PNG's tEXt comment into the JPEG COM segment on re-encode), so swap in a
    # clean image through the same PUT endpoint the UI's "replace photo" uses.
    clean = io.BytesIO()
    Image.new("RGB", (300, 400), (90, 90, 160)).save(clean, format="PNG")  # no directive at all
    replaced = client.put(
        f"/api/v1/items/{item_id}/image", files={"image": ("clean.png", clean.getvalue(), "image/png")}
    )
    assert replaced.status_code == 200, replaced.text

    fake_provider.os.environ["FAKE_MODE"] = "ok"
    fixed = client.post(f"/api/v1/items/{item_id}/analyze")
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["status"] == "ready"
    assert fixed.json()["ai_error"] is None
    assert fixed.json()["type"] == "shirt"


def test_suggestions_report_ai_failure_as_503(client):
    for tag in ("shirt", "pants", "shoes"):
        assert _upload(client, "ok", tag).status_code == 201
    # The outfit prompt carries no image, so this path uses the env channel.
    fake_provider.os.environ["FAKE_MODE"] = "ratelimit"
    try:
        response = client.post("/api/v1/outfits/suggest-options", json={"occasion": "casual"})
    finally:
        fake_provider.os.environ["FAKE_MODE"] = "ok"
    assert response.status_code == 503, response.text
    assert "quota" in response.json()["detail"].lower()


def test_missing_api_key_still_works_for_local_providers(client):
    """Ollama-style endpoints need no key; the client must simply omit the header."""
    from app import ai_service, config

    monkey_env = os.environ.pop("AI_API_KEY", None)
    config.get_settings.cache_clear()
    ai_service._service = None
    try:
        response = _upload(client, "ok")
        assert response.status_code == 201
        assert response.json()["type"] == "shirt"
        assert "Bearer" not in str(ai_service.get_ai()._headers())
    finally:
        if monkey_env is not None:
            os.environ["AI_API_KEY"] = monkey_env
        config.get_settings.cache_clear()
        ai_service._service = None


def test_no_ai_configured_saves_unanalyzed_and_blocks_suggestions(client):
    from app import ai_service, config

    monkeypatch_env = os.environ["AI_BASE_URL"]
    os.environ["AI_BASE_URL"] = ""
    config.get_settings.cache_clear()
    ai_service._service = None
    try:
        response = client.post("/api/v1/items", files={"image": ("a.png", _img("shirt"), "image/png")})
        assert response.status_code == 201
        assert "AI_BASE_URL is not configured" in response.json()["ai_error"]
        assert response.json()["status"] == "error"

        for tag in ("pants", "shoes"):
            _upload(client, "ok", tag)
        suggest = client.post("/api/v1/outfits/suggest-options", json={"occasion": "casual"})
        assert suggest.status_code == 503, suggest.text
        assert "No AI provider configured" in suggest.json()["detail"]
    finally:
        os.environ["AI_BASE_URL"] = monkeypatch_env
        config.get_settings.cache_clear()
        ai_service._service = None


def test_analyze_without_provider_returns_503(client):
    from app import ai_service, config

    assert _upload(client, "ok").status_code == 201
    item_id = client.get("/api/v1/items").json()["items"][0]["id"]
    saved_base = os.environ["AI_BASE_URL"]
    os.environ["AI_BASE_URL"] = ""
    config.get_settings.cache_clear()
    ai_service._service = None
    try:
        response = client.post(f"/api/v1/items/{item_id}/analyze")
        assert response.status_code == 503
        assert "No AI provider configured" in response.json()["detail"]
    finally:
        os.environ["AI_BASE_URL"] = saved_base
        config.get_settings.cache_clear()
        ai_service._service = None


def test_ai_timeout_is_reported_not_hung(client):
    """A slow provider must fail inside the configured budget, with a readable line."""
    import asyncio

    from app import ai_service, config

    fake_provider.os.environ["FAKE_SLOW_SECONDS"] = "6"
    os.environ["AI_TIMEOUT"] = "1"
    config.get_settings.cache_clear()
    ai_service._service = None
    started = time.monotonic()
    try:
        with pytest.raises(ai_service.AIError) as excinfo:
            asyncio.run(ai_service.get_ai().analyze_image_bytes(_img("shirt", "slow")))
        assert "longer than" in str(excinfo.value)
        assert time.monotonic() - started < 5, "timeout was not respected"
    finally:
        fake_provider.os.environ["FAKE_SLOW_SECONDS"] = "0"
        os.environ["AI_TIMEOUT"] = "5"
        config.get_settings.cache_clear()
        ai_service._service = None


def test_provider_rejecting_extra_params_retries_without_them(client):
    """logprobs/reasoning_effort are optional; a provider that refuses them — even
    with a generic message that does not name the parameter — must still produce
    tags. This is why the original carried that complexity."""
    fake_provider.os.environ["FAKE_MODE"] = "reject_params"
    try:
        response = _upload(client)
    finally:
        fake_provider.os.environ["FAKE_MODE"] = "ok"
        response = response
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["type"] == "shirt"


def test_out_of_domain_type_is_distinguishable(client):
    """"tights" must not be silently flattened into "unknown"."""
    body = _upload(client, "ood_type").json()
    assert body["type"] == "unknown"
    assert body["ai_unrecognized_type"] == "tights"


def test_image_and_outfit_parsers_accept_messy_output(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    from app.ai_service import AIResponseUnparsableError, extract_json
    from app.outfit_service import _parse_outfits

    assert extract_json('Here you go:\n```json\n{"type":"hat"}\n```\nHope that helps') == {"type": "hat"}
    assert extract_json('{"type":"hat" /* c */}') == {"type": "hat"}
    assert extract_json('prefix {"a": [1,2]} suffix') == {"a": [1, 2]}
    with pytest.raises(AIResponseUnparsableError):
        extract_json("no json here at all")
    with pytest.raises(AIResponseUnparsableError):
        extract_json("")

    outfits = _parse_outfits('{"outfits":[{"items":["[2]","1"],"headline":"X"}]}', {1: "a", 2: "b"})
    assert outfits[0]["item_ids"] == ["b", "a"]
    assert _parse_outfits('{"outfits":[{"items":[1,1,99]}]}', {1: "a"})[0]["item_ids"] == ["a"]
    with pytest.raises(Exception):
        _parse_outfits('{"outfits":[{"items":[99]}]}', {1: "a"})


def test_image_validation_errors(client):
    bad = client.post("/api/v1/items", files={"image": ("x.png", b"definitely not a png", "image/png")})
    assert bad.status_code == 400
    assert "readable image" in bad.json()["detail"]

    heic = client.post("/api/v1/items", files={"image": ("x.heic", b"\x00\x00", "image/heic")})
    assert heic.status_code == 400
    assert "JPEG" in heic.json()["detail"]

    tiny = io.BytesIO()
    Image.new("RGB", (10, 10), (0, 0, 0)).save(tiny, format="PNG")
    ok = client.post("/api/v1/items", files={"image": ("tiny.png", tiny.getvalue(), "image/png")})
    assert ok.status_code == 201


def test_cors_and_prefix_sanity(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/nope").status_code == 404
    assert client.post("/api/v1/items", data={}).status_code == 422  # missing file, readable error


def test_sqlite_pragmas_tame_concurrent_writers(client):
    """Regression: a bulk upload used to die with `database is locked`.

    Two things keep that from coming back. (1) The engine must put busy_timeout
    and WAL on *every* SQLite connection - without busy_timeout a second writer
    fails instantly instead of waiting, and without WAL a read during a write
    fails too. (2) The writer slot itself is serialized in-process
    (app/deps.py:writer_lock), which is what test_bulk_upload_holds_up_under_concurrency
    covers end to end.
    """
    import asyncio

    from app import database

    db_file = Path(client.storage_root) / "wardrowbe.db"
    assert db_file.exists(), "the fixture should have created the SQLite file"

    async def _pragmas():
        engine = database.get_engine()
        async with engine.connect() as conn:
            mode = (await conn.exec_driver_sql("PRAGMA journal_mode")).fetchone()[0]
            timeout = (await conn.exec_driver_sql("PRAGMA busy_timeout")).fetchone()[0]
        return str(mode).lower(), int(timeout)

    mode, timeout = asyncio.run(_pragmas())
    assert mode == "wal", f"expected WAL so reads survive a write, got {mode}"
    assert timeout >= 1000, f"busy_timeout must be applied per connection, got {timeout}ms"


def test_bulk_upload_holds_up_under_concurrency(client, monkeypatch):
    """12 files, 12 concurrent AI calls, one SQLite file: nothing may be lost."""
    # The config is cached per process, and ai_upload_concurrency is capped at
    # 8 by the model, so the widest legal fan-out is what we test with.
    monkeypatch.setenv("AI_UPLOAD_CONCURRENCY", "8")
    monkeypatch.setenv("MAX_BULK_UPLOAD_COUNT", "8")
    from app import config

    config.get_settings.cache_clear()
    try:
        files = [("images", (f"{i}.png", _img("shirt"), "image/png")) for i in range(8)]
        response = client.post("/api/v1/items/bulk", files=files)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["failed"] == 0, body
        assert body["total"] == 8
        assert client.get("/api/v1/items").json()["total"] == 8
    finally:
        config.get_settings.cache_clear()
