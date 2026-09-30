# =============================================================================
# Wardrowbe trial - ONE image, ONE process pair, one Render Free service.
#
# The original needed six containers (postgres, redis, backend, frontend, an
# arq worker and an image worker) behind Nginx or Caddy. This build has no
# queue and no worker: uploads are analyzed inline by FastAPI, so the UI and the
# API can share a container. Render's free tier bills per service, so one
# service is the point of this file.
#
#   docker build -t wardrowbe-trial .
#   docker run -p 10000:10000 -e AI_API_KEY=... -e AI_BASE_URL=... wardrowbe-trial
#
# Then open http://localhost:10000 . With no DATABASE_URL the app creates a
# SQLite file in STORAGE_DIR, and with no STORAGE_S3_* your photos live on that
# (ephemeral) disk.
# =============================================================================

# --- UI build -----------------------------------------------------------------
FROM node:24-alpine AS ui
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
# Build deps only; the runtime image gets none of node_modules except the
# handful Next's standalone tracer copies below.
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1
# BACKEND_URL is read at request time by the /api/v1 proxy route, never baked
# into the bundle, so this image can be pointed at any host after the build.
RUN npm run build

# --- API + runtime ------------------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

# curl is for Render's health check; tini reaps the two supervised processes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/*
# tini is only a convenience reaper here; if a future base image lacks it the
# entrypoint still works without it (Render restarts the container on exit).

WORKDIR /app/backend
COPY backend/requirements.txt backend/requirements-s3.txt ./
# boto3 comes along so STORAGE_S3_* works without a rebuild - it is the only way
# to keep photos on a free tier, and it costs ~8 MB.
RUN pip install -r requirements.txt -r requirements-s3.txt

COPY backend/ /app/backend/
# Next's standalone output is self-contained except for these three paths.
COPY --from=ui /app/.next/standalone/ /app/frontend/
COPY --from=ui /app/.next/static/ /app/frontend/.next/static/
COPY --from=ui /app/public/ /app/frontend/public/
# next-intl loads messages via a dynamic import; keep the JSON next to the server.
COPY --from=ui /app/messages/ /app/frontend/messages/

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/wardrobe \
    && chown -R appuser:appuser /data /app
USER appuser

ENV STORAGE_DIR=/data/wardrobe \
    PORT=10000 \
    FRONTEND_PORT=10000 \
    BACKEND_PORT=8000 \
    BACKEND_URL=http://127.0.0.1:8000 \
    HOSTNAME=0.0.0.0

EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/" >/dev/null || exit 1

# uvicorn resolves "app.main:app" from the WORKDIR (/app/backend), so no --app-dir needed.
RUN chmod +x /app/backend/entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/app/backend/entrypoint.sh"]
