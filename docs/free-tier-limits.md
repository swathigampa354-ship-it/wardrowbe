# Free-tier limits — what genuinely does not work

Written against the two free hosts this build targets: **Render Free** (web service +
Postgres) and **Hugging Face Spaces** (free CPU tier). Read this before a demo looks broken.

## 1. Storage: the ephemeral-disk problem

Render's free instance filesystem is **container-local**. Anything written to `STORAGE_DIR` —
including the SQLite database when `DATABASE_URL` is empty — is lost on:

- a redeploy (every push),
- a restart or spin-down,

…because the free plans on both hosts have **no persistent disk**: on Render disks are a paid
add-on, and on Hugging Face the runtime disk is wiped on every restart, rebuild or settings
change. A `git push` is therefore a data-wiping event for this app — the property the trial has to
be honest about.

So the honest statement in the UI is the one the code prints: *"local disk is EPHEMERAL —
photos are lost on redeploy/restart/spin-down."* It appears in three places on purpose:

| Where | Evidence |
|---|---|
| startup log | `WARNING wardrowbe.trial: Storage: local disk … is EPHEMERAL` |
| `GET /api/v1/health/storage` | `{"backend":"local","persistent":false,"note":"…"}` |
| `/dashboard/settings` | "Data & limits" card, plus `capabilities.storage.persistent` |

**Workaround that actually persists:** any S3-compatible bucket. Cloudflare R2's free tier
(10 GB) or Backblaze B2 (10 GB) are the cheap options:

```
STORAGE_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com
STORAGE_S3_BUCKET=wardrowbe
STORAGE_S3_ACCESS_KEY=***
STORAGE_S3_SECRET_KEY=***
```

`requirements-s3.txt` (`boto3`) must be installed — the combined Dockerfile includes it. Rows
live on regardless, so with R2 + free Postgres the trial becomes fully durable on free infra.

## 2. Database

| Setup | Survives a restart? | Survives 30 days? |
|---|---|---|
| `DATABASE_URL` empty → SQLite on disk | no | no |
| Render Free Postgres (1 GB) | yes | **no — expires 30 days after creation, deleted 14 days later** |
| Your own Postgres/Neon/Supabase free tier | yes | usually yes (check theirs) |

Render's free Postgres is one 1 GB instance per workspace: no backups, no connection pooling, no
fork. For a demo that is fine; for anything you care about, `pg_dump` it before day 30 and restore
into a paid or third-party instance.

## 3. CPU, RAM and cold starts

Render's free web service: 512 MB RAM on a shared CPU, **750 free instance-hours per workspace per
month**, spin-down after **15 minutes** without inbound traffic and roughly **30–60 s** to wake.
Hugging Face's free `cpu-basic` Space is roomier (2 vCPU / 16 GB, ~50 GB ephemeral disk) but only
sleeps after **48 h** idle — at the cost of a 30–90 s cold start and a wrapper page.
Three consequences for this app:

- image processing (Pillow re-encode of a 12 MP photo) is *slow* on 0.1–0.5 CPU — hence
  `MAX_UPLOAD_SIZE_MB=10` and `MAX_IMAGE_MEGAPIXELS=50`;
- a long request can hit the platform's idle timeout. `POST /api/v1/items/bulk` answers as soon as
  the photos are stored, so it does not; if you raise `MAX_BULK_UPLOAD_COUNT` well past 20, chunk
  uploads client-side (the frontend already chunks at 20);
- 512 MB is why this is **one container with one uvicorn worker**: no worker process and no model
  weights. Keep `API_WORKERS=1` — SQLite serializes writers anyway, and a second worker only adds
  RSS. Instance-hours are shared across free services in a workspace, which is the other reason
  the trial is a single service.

## 4. AI quota

Nothing in the free tier is unlimited:

- The documented default (`gemini-2.5-flash` through AI Studio) is rate-limited per minute and per
  day: published free-tier figures for Flash-class models sit around **10–15 requests/min** with a
  few hundred to ~1,500 requests/day, and Google has revised these quotas more than once in the
  last year. They are the provider's limits, not the app's — treat them as "one person clicking
  around", never as traffic headroom.
- Model availability on the free tier moves too (in 2026 the free list is Flash / Flash-Lite, with
  Pro behind billing). If the model 404s in your project, change `AI_VISION_MODEL` /
  `AI_TEXT_MODEL`; nothing else in this build is provider-specific.
- On a quota error the API surfaces the provider's own reason (`429 → "quota"`) on the item as
  `ai_error`, and the outfit endpoint returns 503 with that message. Uploads are never lost to it.
- `AI_VISION_MODEL=a,b,c` rotates models across calls, which is the usual way to survive a
  per-model limit.
- `AI_MAX_RETRIES=2` with backoff; a provider that 400s on an *optional* parameter
  (`logprobs`, `reasoning_effort`) has that parameter dropped and the call retried without
  consuming the retry budget.
- There is no "free trial credit" being claimed anywhere in this build — if your provider's free
  tier ends, so does the demo. Set `AI_API_KEY` to a paid key and nothing else changes.

## 5. Things the free tier makes impossible (by design here)

| Missing | Why |
|---|---|
| Scheduled morning outfit notifications | needs a second always-on process (cron/worker) — that is a paid instance |
| Background removal, image rotation, multi-photo galleries | needed `rembg`/onnxruntime (~500 MB, no free CPU tier runs it comfortably) and an image worker |
| Family sharing / voting | needs auth, invitations, a real user model |
| Duplicate detection (pHash) | extra dep for a feature nobody in a demo exercises |
| `GET /health/ready` deep checks (queue depth, worker lag) | there is no queue to be lagged; the endpoint stays, reporting SQLite/AI/storage |
| Websockets / SSE progress | Render Free + one worker: polling `/items/tagging-progress` is cheaper and works behind their proxy |

## 6. Hugging Face Spaces (Docker SDK, free CPU basic)

Works and is a reasonable second choice:

- the same image runs; set `FRONTEND_PORT=7860` (Spaces routes 7860) and `BACKEND_PORT=8000`;
- free `cpu-basic` is 2 vCPU / 16 GB with a ~50 GB **ephemeral** disk, wiped on restart, rebuild or
  any settings change; the Space sleeps after 48 h idle; persistent storage is a paid add-on;
- you *can* persist by committing `STORAGE_DIR` back into the repo on shutdown (the usual Spaces
  trick) — the trial does not implement that, because silently rewriting a git repo from a demo
  app is a bad trade;
- a Space has no custom domain and adds a "community" wrapper page.

Use Render for a demo you want to look like a product; use a Space when you want a zero-config
public playground.

## 7. Cost when you outgrow it

Nothing here needs restructuring: add `API_WORKERS=2` on a paid instance, or set
`RUN_FRONTEND=false`-style split deployment (the `frontend/Dockerfile` is kept for exactly the
two-service case: UI on a free static-ish service, API next to the Postgres). Scaling from
SQLite → Postgres is one env var (`DATABASE_URL`), because every column is portable
(`String(36)` uuids, `JSON`, `Text`, `Float` — no ARRAY, no JSONB operators, no `server_default`).

## Sources for the numbers above

- Render free-tier limits (15-minute spin-down, ~30-60 s wake, 512 MB RAM, 750 free
  instance-hours per workspace, ephemeral filesystem): <https://render.com/docs/free>; persistent
  disks are paid: <https://render.com/docs/disks>
- Render free Postgres: 1 GB, expires after 30 days with a 14-day grace period:
  <https://render.com/docs/free-postgres>
- Hugging Face Spaces: `cpu-basic` sleeps after 48 h idle (not configurable on the free flavor):
  <https://huggingface.co/docs/huggingface_hub/main/en/guides/manage-spaces>; disk is ephemeral
  unless you buy persistent storage: <https://huggingface.co/docs/hub/spaces-persistent-storage>
- Gemini free-tier request quotas (per-minute and per-day, model-dependent, revised more than once
  in 2025-2026): <https://ai.google.dev/gemini-api/docs/rate-limits>

Provider quotas are the numbers most likely to have drifted since this file was written. The app's
behaviour does not depend on them: a quota error is a readable message on the item, never a lost
photo.
