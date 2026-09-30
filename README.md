# Wardrowbe — free-tier trial

A single-container build of [wardrowbe](https://github.com/Anyesh/wardrowbe) that does the one
thing worth demonstrating and nothing else:

**upload clothing photos → an AI tags each one → the app assembles ~3 outfits → you accept or
reject them in a web UI.**

Everything that made the original expensive to run — Redis, arq workers, a scheduled-notification
loop, family sharing, an image-processing GPU-ish worker, OIDC, Nginx, Kubernetes — is gone. One
container, one `docker build`, one free Render service.

```
docker build -t wardrowbe-trial .
docker run --rm -p 10000:10000 \
  -e AI_API_KEY=<your key> \
  wardrowbe-trial
# → http://localhost:10000
```

No `AI_API_KEY`? The container still runs: uploads are stored, the wardrobe works, and outfit
generation returns a readable 503 telling you which variable to set.

---

## What is in here

| Path | What it is |
|---|---|
| `backend/` | FastAPI. `app/api/{items,outfits,misc}.py` hold every route; `app/ai_service.py` is the OpenAI-compatible client. |
| `frontend/` | Next.js 14 (standalone output). The original UI, minus every screen that talked to a removed service. |
| `Dockerfile` | The whole app in one image. |
| `.env.example` | Every variable the trial reads, with what breaks if you omit it. |
| `docs/architecture.md` | Request-flow diagram + why each removal was safe. |
| `docs/free-tier-limits.md` | What genuinely does not work on a free tier, and the honest workaround. |
| `docs/strip-plan.md` | Removed vs retained, component by component. |

## Running it

### Docker (closest to the deployed shape)

```bash
cp .env.example .env          # fill in AI_API_KEY
docker build -t wardrowbe-trial .
docker run --rm -p 10000:10000 --env-file .env wardrowbe-trial
```

`STORAGE_DIR` defaults to `/data/wardrobe`, which is container-local and **ephemeral** — exactly
like Render Free. Mount a volume (`-v $PWD/storage:/data/wardrobe`) if you want the demo to
survive a restart of the container.

### Without Docker

```bash
# API
cd backend && python3 -m pip install -r requirements.txt
STORAGE_DIR=$PWD/../storage AI_API_KEY=*** \
  AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai \
  AI_VISION_MODEL=gemini-2.5-flash AI_TEXT_MODEL=gemini-2.5-flash \
  python3 -m uvicorn app.main:app --port 8000

# UI (separate shell) — it proxies /api/v1 to $BACKEND_URL at request time
cd frontend && npm ci && BACKEND_URL=http://127.0.0.1:8000 npm run dev
```

### Verifying

```bash
cd backend
python3 -m pytest        # 18 tests: AI failure modes, image validation, CORS, SQLite concurrency
python3 -m tests.e2e     # 59 checks: upload → tag → edit → re-analyze → 3 outfits → accept → cleanup
cd ../frontend
npx tsc --noEmit         # type-clean against the stripped API
npx next build           # production build (type + lint errors fail the build)
```

`tests/e2e` and `tests/test_ai_failures.py` run against a stdlib stub provider
(`backend/tests/fake_provider.py`) — no network, no API key, no quota. That stub is also what
makes the failure-path tests real: bad key, rate limit, truncated JSON, non-JSON, timeouts,
out-of-vocabulary clothing types.

## The demo flow, in order

1. Open `/dashboard` — a starter card tells you the wardrobe is empty.
2. `/dashboard/wardrobe` → **Add item** (one photo) or **Bulk upload** (drag a folder).
3. Each upload returns as soon as the photo is stored; the AI tags arrive a few seconds later and
   the card flips from "Analyzing…" to the detected type/colours. A failed analysis shows the
   provider's actual error and a **Re-analyze** button, never a lost photo.
4. `/dashboard/suggest` → pick an occasion → three complete outfits, each with a headline,
   highlights and a styling tip.
5. Accept (goes to `/dashboard/outfits`), reject, or "try another".
6. `/dashboard/outfits` → open one → accept/reject/delete, see the items.
7. `/dashboard/settings` → location (weather), default occasion, temperature unit, plus a live
   read-out of the AI provider, storage mode and limits.

## AI configuration

Any OpenAI-compatible `/chat/completions` endpoint works. Default is Google's Gemini free tier — the
mainstream free tier that still takes image input (rate-limited to a few hundred calls a day: fine
for one person clicking around, not for traffic):

```
AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
AI_API_KEY=***
AI_VISION_MODEL=gemini-2.5-flash
AI_TEXT_MODEL=gemini-2.5-flash
```

OpenAI, OpenRouter, Groq (text only — no image input), a local Ollama/vLLM server: change those
three values and nothing else. Comma-separate `AI_VISION_MODEL` to rotate across models when one
hits its rate limit. Keys are read only by the API process — nothing AI-related is ever bundled
into the frontend.

## Deploying to Render (free)

Full click-through in [`docs/render-deploy.md`](docs/render-deploy.md). Summary: **New →
Blueprint** (or *Web Service*), repo = this project's `trial-free-tier` branch, Docker runtime,
instance type **Free**, path to Dockerfile `/Dockerfile`, health check path `/`, and the env vars
from `.env.example`. Optionally attach a free Postgres instance (1 GB, **expires 30 days
after creation**) so rows survive a restart; photos still do not unless you point `STORAGE_S3_*` at a real
bucket.

## Deliberate limits (read this before judging a demo)

- **Render Free has no persistent disk.** Photos on local disk are deleted on redeploy, restart
  or spin-down. The app says so on startup, in `/health/storage`, and on the settings page.
- **Render's free Postgres expires 30 days after creation** (deleted 14 days later) — dump it
  before then, or run the demo with `STORAGE_DIR` on a mounted volume locally instead.
- **Spin-down**: the first request after idle pays ~50 s of cold start.
- No multi-user, no sharing, no background queue, no email/ntfy notifications, no analytics.
