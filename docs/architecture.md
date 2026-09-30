# Architecture — trial build

## Request flow (one container)

```
                        browser
                          │
                          ▼
        ┌───────────────────────────────────────┐
        │  Next.js (node server.js, :10000)     │
        │  app/dashboard/{wardrobe,suggest,     │
        │                 outfits,settings}     │
        │                                       │
        │  app/api/v1/[...path]/route.ts        │  ← reads BACKEND_URL per request,
        │    "proxy everything to the API"      │    so one image can point anywhere
        └───────────────┬───────────────────────┘
                        │ http://127.0.0.1:8000
                        ▼
        ┌───────────────────────────────────────┐   ┌──────────────────────────────┐
        │  FastAPI (uvicorn, :8000, loopback)   │──▶│ OpenAI-compatible endpoint   │
        │                                       │   │ AI_BASE_URL + AI_API_KEY     │
        │  /api/v1/items      upload, analyze   │◀──│  vision: tag a garment       │
        │  /api/v1/outfits    generate, decide  │   │  text:   assemble the look   │
        │  /api/v1/images/*   read back a blob  │   └──────────────────────────────┘
        │  /api/v1/weather    Open-Meteo        │──▶ api.open-meteo.com (no key)
        └───────┬───────────────────────┬───────┘
                │                       │
                ▼                       ▼
     ┌────────────────────┐   ┌──────────────────────────┐
     │ SQLite file        │   │ blob store               │
     │ (or Postgres if    │   │ LocalBlobStore by default│
     │  DATABASE_URL set) │   │ S3BlobStore if STORAGE_S3_*
     └────────────────────┘   └──────────────────────────┘
```

`entrypoint.sh` starts both processes and exits if either dies, so the platform restarts the
container instead of serving half an app.

## Upload: synchronous by design

```
POST /api/v1/items/bulk  (multipart, ≤ MAX_BULK_UPLOAD_COUNT files)
  ├─ read bytes, enforce MB + megapixel ceilings        → 400 with a readable reason
  ├─ process_upload(): EXIF orientation, JPEG re-encode,
  │  thumbnail(400) + medium(800)                       → Pillow, in parallel per file
  ├─ store.put(...)                                     → the photo is durable NOW
  ├─ INSERT clothing_items (status='processing')        → one SQLite write window
  └─ 200 BulkUploadResponse                             ← user waits ~1 s, not 30 s

   (spawned after the response, as background tasks)
  └─ analyze_item_row(item_id)
       ├─ POST AI_BASE_URL/chat/completions (thumbnail)
       ├─ extract_json → ClothingTags (pydantic)
       ├─ apply_tags(item, tags) → type, subtype, colours, pattern, material,
       │  formality, style, season, fit, completeness, confidence
       └─ status='ready'  — or status='error' + ai_error (the photo is never rolled back)
```

The UI learns the result the same way the original did — `GET /api/v1/items/tagging-progress`,
polled every 5 s while anything is processing — but there is no queue behind it: "processing" just
means "a task in this process is still working on it". `POST /api/v1/items` (single upload) stays
fully synchronous so one-shot API callers get tags in the response; `?strict=true` turns an AI
failure into a 503 instead of a stored-but-untagged row.

A restart mid-analysis cannot leave a spinner forever, so startup marks orphaned `processing`
rows as `error` with "Analysis was interrupted when the server restarted" and the UI offers
Re-analyze.

## Outfit generation: no scheduler, no worker

`POST /api/v1/outfits/suggest-options` scores the wardrobe locally (formality, season, weather
fit, colour palette, layer quotas, wear history), hands the shortlist to the text model, and
parses 3 looks. Weather comes from Open-Meteo when `WEATHER_ENABLED=true`, or from
`weather_override` (the UI's manual weather picker) — never from a cron.

## Why each removal was safe

Nothing was deleted on a hunch: each removal below was traced from the component that used it
down to the service that served it, and the frontend was rebuilt afterwards so no dead import or
type error survived.

| Removed | Was used by | Replacement |
|---|---|---|
| Redis + arq (`worker.py`, `image_worker.py`) | every AI call, background removal, rotation, re-analysis | inline `await` in the request, plus bounded `asyncio.Semaphore` |
| `apscheduler`, ntfy, Mattermost, SMTP | morning push notifications, family digests | nothing — the trial has no push path at all |
| OIDC / NextAuth / user table writes | multi-user login | one implicit demo user; `REQUIRE_AUTH` + `DEMO_PASSWORD` optional |
| Family sharing, pairings, ratings | invites, votes, "who wore what" | removed from nav, routes, hooks and `messages/` |
| Learning engine, analytics | ranking feedback loops | accept/reject counters still increment; nothing reads them |
| rembg + u2net (~500 MB image layer) | background removal, restore-original | upload the photo as-is |
| Alembic | 20+ tables of migrations | `create_all()` over 4 tables |
| IP geolocation fallback | privacy-sensitive coarse location | browser geolocation only, else server default lat/lon |
| Nginx, Caddy, K8s manifests | multi-service routing | Render's own TLS/routing on the single service |
| Multi-image galleries, rotate, wash log | per-item image management | one image per item; `PUT /items/{id}/image` replaces it |

## Files that used to be here

`docker-compose.yml` had 6 services (postgres, redis, backend, frontend, worker, image-worker);
`backend/Dockerfile` installed `rembg` and pre-downloaded the `u2net` model at build time;
`k8s/` and `nginx/` described the multi-container topology. All replaced by this `Dockerfile`.

## Concurrency notes (what SQLite forced)

`store_and_analyze()` keeps its transaction open while the AI runs, so N parallel uploads used to
mean N overlapping writers on a database that admits one — a 4-file bulk upload could 500 with
`database is locked`. Three things fix it:

1. `PRAGMA journal_mode=WAL; busy_timeout=10000` on every SQLite connection
   (`app/database.py:_sqlite_pragmas`).
2. A process-wide, task-reentrant writer lock (`app/deps.py:writer_lock`) that the bulk path and
   the synchronous single-upload path hold while they write.
3. Committing the implicit demo user inside `get_current_user`, because a flushed-but-uncommitted
   INSERT would otherwise squat on the write slot for the whole request.

`tests/test_ai_failures.py::test_bulk_upload_holds_up_under_concurrency` is the regression test
for the original failure; `test_sqlite_pragmas_tame_concurrent_writers` pins the pragmas.
On Postgres none of this costs anything — the lock is uncontended there.
