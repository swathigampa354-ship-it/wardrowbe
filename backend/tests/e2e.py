"""End-to-end workflow test: stub provider -> upload -> analyze -> wardrobe ->
generate outfits -> accept -> history. No network, no API key, no Docker.

    python -m tests.e2e            # uses a random free port for the stub
"""

import io
import os
import socket
import tempfile
import threading
import time
from contextlib import closing

_TMP = tempfile.mkdtemp(prefix="wardrowbe-e2e-")
os.environ.setdefault("STORAGE_DIR", _TMP)
os.environ["DEBUG"] = "true"
os.environ["WEATHER_ENABLED"] = "false"  # keep the run hermetic
os.environ["AI_MAX_RETRIES"] = "1"


def _free_port() -> int:
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_image(
    color: tuple[int, int, int],
    *,
    size=(400, 520),
    fmt: str = "PNG",
    tag: str | None = None,
    mode: str | None = None,
) -> bytes:
    from PIL import Image, PngImagePlugin

    img = Image.new("RGB", size, color)
    for x in range(60, 340, 40):  # some texture so it is a real photo-ish file
        for y in range(60, 460, 60):
            img.paste(tuple(min(255, c + 25) for c in color), (x, y, x + 18, y + 26))
    buf = io.BytesIO()
    if (tag or mode) and fmt == "PNG":
        directive = " ".join(f"WARDROBE:{k}={v}" for k, v in (("tag", tag), ("mode", mode)) if v)
        meta = PngImagePlugin.PngInfo()
        meta.add_text("comment", directive)
        img.save(buf, format=fmt, quality=92, pnginfo=meta)
    else:
        img.save(buf, format=fmt, quality=92)
    return buf.getvalue()


def wait_for_tagging(client, timeout_s: float = 20.0) -> dict:
    """Poll /items/tagging-progress until nothing is processing any more.

    Bulk uploads answer before the AI runs (the row is durable first), so any
    assertion about tags has to wait for the background pass the way the UI does.
    """
    deadline = time.time() + timeout_s
    progress: dict = {}
    while time.time() < deadline:
        progress = client.get("/api/v1/items/tagging-progress").json()
        if not progress.get("processing") and not progress.get("queued"):
            return progress
        time.sleep(0.2)
    raise AssertionError(f"tagging did not settle within {timeout_s}s: {progress}")


PASS, FAIL = 0, 0
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  \033[32mPASS\033[0m {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} — {detail}")
        print(f"  \033[31mFAIL\033[0m {name} {detail}")


def main() -> int:
    from fastapi.testclient import TestClient

    from tests import fake_provider

    port = _free_port()
    os.environ["FAKE_MODE"] = "ok"
    server = threading.Thread(
        target=fake_provider.serve, args=(port,), daemon=True, name="fake-provider"
    )
    server.start()
    time.sleep(0.3)

    from app import config

    config.get_settings.cache_clear()
    os.environ["AI_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    os.environ["AI_API_KEY"] = "test-key-not-a-secret"
    os.environ["AI_VISION_MODEL"] = "fake-vision"
    os.environ["AI_TEXT_MODEL"] = "fake-text"
    config.get_settings.cache_clear()
    from app import ai_service

    ai_service.get_ai.cache_clear() if hasattr(ai_service.get_ai, "cache_clear") else None
    ai_service._service = None

    from app.main import app

    with TestClient(app) as client:
        print("\n\033[1m1. Boot and self-description\033[0m")
        r = client.get("/api/v1/health")
        check("GET /health is 200", r.status_code == 200, r.text[:200])
        r = client.get("/api/v1/health/ready")
        check(
            "database healthy after schema init",
            r.json()["checks"]["database"] == "healthy",
            r.text[:200],
        )
        r = client.get("/api/v1/auth/status")
        check("auth reports no-login mode", r.json()["auth_required"] is False, r.text[:200])
        r = client.get("/api/v1/capabilities")
        caps = r.json()
        check("capabilities reports AI configured", caps["ai"]["vision"] is True, r.text[:200])
        check(
            "capabilities reports no async queue",
            caps["features"]["async_queue"] is False,
            r.text[:200],
        )
        check(
            "storage flagged non-persistent", caps["storage"]["persistent"] is False, r.text[:200]
        )

        # The settings page PATCHes this shape; it once shipped with a deleted session
        # variable in the handler, which no test touched, so every save 500'd.
        r = client.patch(
            "/api/v1/users/me", json={"display_name": "Demo", "temperature_unit": "celsius"}
        )
        check(
            "profile update persists",
            r.status_code == 200 and r.json()["display_name"] == "Demo",
            r.text[:200],
        )
        check(
            "profile unit survives a reload",
            client.get("/api/v1/users/me").json()["temperature_unit"] == "celsius",
        )
        r = client.patch("/api/v1/users/me", json={"temperature_unit": "kelvin"})
        check("bad profile value rejected", r.status_code == 422, r.text[:200])

        print("\n\033[1m2. Upload one garment -> AI analysis -> metadata\033[0m")
        r = client.post(
            "/api/v1/items",
            files={"image": ("shirt.png", make_image((90, 130, 200), tag="shirt"), "image/png")},
            data={"name": "Oxford shirt"},
        )
        check("upload returns 201", r.status_code == 201, r.text[:300])
        item = r.json()
        check("type analysed", item["type"] == "shirt", str(item.get("type")))
        check("subtype analysed", item["subtype"] == "oxford", str(item.get("subtype")))
        check(
            "primary colour analysed",
            item["primary_color"] == "light-blue",
            str(item.get("primary_color")),
        )
        check("pattern analysed", item["pattern"] == "solid", str(item.get("pattern")))
        check("material analysed", item["material"] == "cotton", str(item.get("material")))
        check("formality analysed", item["formality"] == "smart-casual", str(item.get("formality")))
        check(
            "season analysed",
            item["season"] == ["spring", "fall", "all-season"],
            str(item.get("season")),
        )
        check("fit analysed", item["fit"] == "regular", str(item.get("fit")))
        check("status ready (no queue)", item["status"] == "ready", str(item.get("status")))
        check("ai_processed true", item["ai_processed"] is True, str(item.get("ai_processed")))
        check(
            "confidence in 0..1",
            0 <= float(item["ai_confidence"] or 0) <= 1,
            str(item.get("ai_confidence")),
        )
        check(
            "description captured",
            bool(item.get("ai_description")),
            str(item.get("ai_description")),
        )
        check("image_url served", bool(item.get("image_url")), str(item.get("image_url")))
        img = client.get(item["image_url"])
        check(
            "image bytes retrievable",
            img.status_code == 200 and len(img.content) > 500,
            f"{img.status_code}",
        )
        thumb = client.get(item["thumbnail_url"])
        check(
            "thumbnail bytes retrievable",
            thumb.status_code == 200 and len(thumb.content) > 200,
            f"{thumb.status_code}",
        )

        print("\n\033[1m3. Wardrobe list\033[0m")
        r = client.get("/api/v1/items", params={"page": 1, "page_size": 20})
        listing = r.json()
        check(
            "list returns the item",
            listing["total"] == 1 and listing["items"][0]["id"] == item["id"],
            r.text[:200],
        )
        r = client.get("/api/v1/items", params={"type": "shoes"})
        check("type filter excludes the shirt", r.json()["total"] == 0, r.text[:120])
        r = client.get("/api/v1/items", params={"search": "oxford"})
        check("search matches", r.json()["total"] == 1, r.text[:120])
        r = client.get("/api/v1/items/tagging-progress")
        check("tagging-progress endpoint alive", r.json()["completed"] == 1, r.text[:150])

        print("\n\033[1m4. Bulk upload the rest of a wardrobe\033[0m")
        wardrobe = [
            ("pants", (20, 30, 50)),
            ("jeans", (40, 60, 110)),
            ("shoes", (30, 30, 30)),
            ("t-shirt", (240, 240, 240)),
            ("sweater", (120, 100, 70)),
            ("jacket", (60, 70, 60)),
            ("boots", (50, 30, 20)),
        ]
        files = [
            ("images", (f"{name}.png", make_image(color, tag=name), "image/png"))
            for name, color in wardrobe
        ]
        r = client.post("/api/v1/items/bulk", files=files, data={"skip_ai": "false"})
        check("bulk upload 200", r.status_code == 200, r.text[:300])
        bulk = r.json()
        check("bulk reports 7 successful", bulk["successful"] == 7, str(bulk)[:300])
        check("bulk reports 0 failed", bulk["failed"] == 0, str(bulk)[:300])
        check(
            "bulk answers before the AI finishes",
            all(x["type"] == "unknown" or x["error"] is not None for x in bulk["results"]),
            "bulk should not block on analysis: " + str({x["type"] for x in bulk["results"]}),
        )
        r = client.get("/api/v1/items", params={"page_size": 100})
        check("wardrobe now holds 8 items", r.json()["total"] == 8, str(r.json()["total"]))
        wait_for_tagging(client)
        items = client.get("/api/v1/items", params={"page_size": 100}).json()["items"]
        types = {i["type"] for i in items}
        check("bulk items tagged per-garment", {"pants", "jeans", "shoes"} <= types, str(types))
        check(
            "every item reaches a terminal status",
            all(i["status"] in ("ready", "error") for i in items),
            str([(i["name"], i["status"]) for i in items if i["status"] not in ("ready", "error")]),
        )

        print("\n\033[1m5. Edit an item manually\033[0m")
        r = client.patch(f"/api/v1/items/{item['id']}", json={"favorite": True, "brand": "Uniqlo"})
        check(
            "patch persists favorite+brand",
            r.json()["favorite"] is True and r.json()["brand"] == "Uniqlo",
            r.text[:200],
        )

        print("\n\033[1m6. Re-analyze on demand\033[0m")
        r = client.post(f"/api/v1/items/{item['id']}/analyze")
        check("re-analyze 200", r.status_code == 200, r.text[:200])
        check("re-analyze keeps tags", r.json()["type"] == "shirt", r.text[:200])

        print("\n\033[1m7. Generate outfits\033[0m")
        r = client.post(
            "/api/v1/outfits/suggest-options",
            json={
                "occasion": "office",
                "weather_override": {
                    "temperature": 8,
                    "feels_like": 6,
                    "humidity": 70,
                    "precipitation_chance": 60,
                    "condition": "rain",
                },
            },
        )
        check("suggest-options 200", r.status_code == 200, r.text[:300])
        outfits = r.json()
        check("three options returned", len(outfits) == 3, str(len(outfits)))
        first = outfits[0]
        check("outfit has items", len(first["items"]) >= 3, str(len(first["items"])))
        check(
            "outfit has headline",
            bool(first.get("headline") or first.get("name")),
            str(first)[:200],
        )
        check(
            "outfit has highlights",
            len(first.get("highlights") or []) >= 1,
            str(first.get("highlights")),
        )
        check(
            "outfit has styling tip", bool(first.get("style_notes")), str(first.get("style_notes"))
        )
        check(
            "outfit item images resolve",
            all(i.get("thumbnail_url") for i in first["items"]),
            str(first["items"][:1]),
        )
        check(
            "layer_type present for UI grouping",
            all(i.get("layer_type") for i in first["items"]),
            str(first["items"][:1]),
        )
        check(
            "outfits persisted", client.get("/api/v1/outfits").json()["total"] == 3, "history empty"
        )

        print("\n\033[1m8. Accept / reject / feedback\033[0m")
        r = client.post(f"/api/v1/outfits/{first['id']}/accept")
        check("accept sets status", r.json()["status"] == "accepted", r.text[:200])
        r = client.get(f"/api/v1/items/{first['items'][0]['id']}")
        check(
            "accepted items get wear_count",
            r.json()["wear_count"] == 1,
            str(r.json()["wear_count"]),
        )
        r = client.post(f"/api/v1/outfits/{outfits[1]['id']}/reject")
        check("reject sets status", r.json()["status"] == "rejected", r.text[:200])
        r = client.post(f"/api/v1/outfits/{outfits[2]['id']}/skip")
        check("skip sets status", r.json()["status"] == "skipped", r.text[:200])
        r = client.post(
            f"/api/v1/outfits/{first['id']}/feedback", json={"rating": 4, "comment": "Great"}
        )
        check("feedback stored", r.json()["feedback"]["rating"] == 4, r.text[:200])

        print("\n\033[1m9. Delete removes storage objects\033[0m")
        victim = item["image_url"].split("/images/")[1].split("?")[0]
        r = client.delete(f"/api/v1/items/{item['id']}")
        check("delete returns 204", r.status_code == 204, r.text[:200])
        check("image file gone", not os.path.exists(os.path.join(_TMP, victim)), victim)
        check(
            "list reflects deletion",
            client.get("/api/v1/items").json()["total"] == 7,
            "still there",
        )

        print("\n\033[1m10. Failure handling\033[0m")
        r = client.post(
            "/api/v1/items", files={"image": ("x.png", b"not an image at all", "image/png")}
        )
        check(
            "invalid image -> 400 with readable text",
            r.status_code == 400 and "readable image" in r.text,
            r.text[:200],
        )
        r = client.post(
            "/api/v1/items", files={"image": ("x.txt", make_image((1, 2, 3)), "text/plain")}
        )
        check("wrong content type rejected", r.status_code in (400, 415), r.text[:200])
        r = client.post("/api/v1/items", files={"image": ("empty.png", b"", "image/png")})
        check("empty file rejected", r.status_code == 400 and "empty" in r.text, r.text[:200])
        big = make_image((5, 5, 5), size=(9000, 9000), fmt="PNG")
        r = client.post("/api/v1/items", files={"image": ("huge.png", big, "image/png")})
        check(
            "oversized image rejected clearly",
            r.status_code in (400, 413) and any(w in r.text for w in ("megapixel", "MB")),
            r.text[:220],
        )
        r = client.post(
            "/api/v1/items/bulk",
            files=[("images", (f"{i}.png", make_image((i, i, i)), "image/png")) for i in range(25)],
        )
        check(
            "bulk over the cap rejected",
            r.status_code == 400 and "Maximum 20" in r.text,
            r.text[:200],
        )

        # Empty wardrobe
        client.post(
            "/api/v1/items/bulk/delete", json={"item_ids": []}
        )  # no-op, keeps shape explicit
        remaining = client.get("/api/v1/items", params={"page_size": 100}).json()["items"]
        for row in remaining:
            client.delete(f"/api/v1/items/{row['id']}")
        r = client.post("/api/v1/outfits/suggest-options", json={"occasion": "casual"})
        check(
            "empty wardrobe -> 400 with guidance",
            r.status_code == 400 and "empty" in r.json()["detail"].lower(),
            r.text[:220],
        )

    return PASS, FAIL


if __name__ == "__main__":
    p, f = main()
    print(f"\n{p} checks passed, {f} failed")
    for line in FAILURES:
        print("  !", line)
    raise SystemExit(1 if f else 0)
