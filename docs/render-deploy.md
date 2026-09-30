# Deploying the trial on Render Free (one service)

No deploy was run for you: this session has no Render credential, so these are the exact
click-throughs instead. Everything below was validated as far as it goes locally — the image's two
processes (`node server.js` + `uvicorn`) were run together and the full 8-step workflow was
exercised through the UI's own proxy — but the `docker build` itself was **not** executed here,
because this sandbox has no Docker/podman/kaniko binary and no container socket. Budget one
`docker build` on your machine before pushing, or read the "if the build fails" list at the
bottom.

## A. Push this branch (already done for you)

Branch `trial-free-tier` on your fork contains the trial. Render builds from a branch, so pick
that branch in the next step.

## B. Create the service

1. [render.com](https://dashboard.render.com) → **New +** → **Web Service** (use *Blueprint* only
   if you add a `render.yaml`; there isn't one in this repo on purpose — a free Postgres attached
   through the UI needs no file).
2. Connect the repo, choose branch **`trial-free-tier`**.
3. **Runtime: Docker** — Render auto-detects `/Dockerfile`. Set:
   - *Dockerfile Path*: `/Dockerfile`
   - *Docker Command*: leave empty (the image's `ENTRYPOINT` is the supervisor)
   - *Environment*: none needed; the build stage uses `npm ci` only
4. **Instance Type: Free** (512 MB). Region: nearest to you — Singapore for India.
5. **Health Check Path**: `/`
   (Render only accepts a path, and `/` is served by the UI on the public port — `/api/v1/health`
   is *not* reachable on the public port, since the API listens on loopback inside the container.)

## C. Environment variables

Only `AI_API_KEY` is required for a meaningful demo. Everything else has a working default — see
`.env.example`.

| Key | Value |
|---|---|
| `AI_API_KEY` | your key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `AI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai` (already the default) |
| `AI_VISION_MODEL` / `AI_TEXT_MODEL` | `gemini-2.5-flash` (already the default) |
| `AI_UPLOAD_CONCURRENCY` | `2`–`3` |
| `MAX_BULK_UPLOAD_COUNT` | `20` |
| `WEATHER_ENABLED` | `true` |
| `DEFAULT_LATITUDE` / `DEFAULT_LONGITUDE` / `DEFAULT_LOCATION` | e.g. `17.385` / `78.4867` / `Hyderabad,Telangana,IN` |
| `DEBUG` | `false` |

Do **not** add any `NEXT_PUBLIC_*` AI variable — nothing in the frontend reads a key, and that is
deliberate. `REQUIRE_AUTH` stays `false`; the trial has one implicit demo user.

## D. Optional: the free Postgres (survives restarts, expires after 30 days)

**New +** → **PostgreSQL** → *Free* → name it `wardrowbe-trial-db`. Render injects
`DATABASE_URL` (or whatever internal value you copy) and `init_db()` creates the four tables on
first boot — there is no migration step. Set it as `DATABASE_URL` on the web service.

Without it the app writes `wardrowbe.db` next to `STORAGE_DIR`; on free-tier local disk that file
dies on the next deploy.

## E. Optional: keep photos across redeploys (free R2)

Add to the web service: `STORAGE_S3_ENDPOINT`, `STORAGE_S3_BUCKET`, `STORAGE_S3_ACCESS_KEY`,
`STORAGE_S3_SECRET_KEY`, `STORAGE_S3_REGION=auto`. `GET /api/v1/health/storage` flips to
`{"backend":"s3","persistent":true}` — that endpoint is the honest check for whether your deploy
is actually durable, and the settings page shows the same answer.

## F. First run

1. Open the service URL. The first request after 15 idle minutes takes ~30–60 s (cold start).
2. `/healthz` should return 200 quickly; `GET /api/v1/health/features` tells you AI, DB and
   storage state at a glance.
3. Upload 5–8 garment photos in `/dashboard/wardrobe`, watch the cards flip from "Analyzing…",
   then generate three outfits in `/dashboard/suggest`.
4. Deploy log to check on every boot:

```
Database ready (sqlite)
Auth: none (single demo user)
AI: https://generativelanguage.googleapis.com/v1beta/openai vision=gemini-2.5-flash ...
WARNING Storage: local disk /data/wardrobe is EPHEMERAL on Render Free - ...
```

If the last line is missing, `STORAGE_S3_*` took effect (or you should double-check it).

## G. If the build fails

| Symptom | Cause / fix |
|---|---|
| `npm ci` ERESOLVE | lockfile drift after dependency removal — re-run `npm install` in `frontend/` and commit the refreshed `package-lock.json` |
| `output: 'standalone'` … `server.js: No such file` | `next.config.js` was edited; the root Dockerfile depends on standalone output |
| Build killed, out of memory | Render's free *build* instance is small; set *Build Type* to the smallest paid instance for one build, then switch back |
| Container starts, `/` 502s | `FRONTEND_PORT` mismatch — Render sends traffic to `PORT`; the entrypoint reads `FRONTEND_PORT` and defaults it to 10000, and Render's free tier forwards `PORT=10000`. Set `FRONTEND_PORT=$PORT` explicitly if you change the port |
| Uploads 500 with `database is locked` | you set `API_WORKERS>1` on SQLite — writers must share one process. Put it back to `1` or attach Postgres |
| `429` from the provider | free-tier quota; `AI_VISION_MODEL=a,b` rotates models, `AI_UPLOAD_CONCURRENCY=1` spaces calls |
| Photos gone after a push | expected on local disk — see §E |

## H. Hugging Face Space instead

New Space → **SDK: Docker** → hardware *CPU basic (free)* → build from this repo's
`trial-free-tier` branch (or push the image). Add secrets `AI_API_KEY` (and optionally
`AI_BASE_URL`, model names) and set `FRONTEND_PORT=7860`, `BACKEND_PORT=8000`. The same
ephemeral-disk caveat applies (wiped on restart/rebuild), but the free container is bigger and
only sleeps after 48 h idle, so it is the better choice for a long-lived demo you do not want to
warm up by hand.
