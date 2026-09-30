# UI-only image. You do NOT need this for the trial: the root ../Dockerfile
# already builds Next.js in standalone mode and ships it inside the same image
# as the API. Use this one only for the two-service split (UI on one free
# service, API next to your Postgres on another).
#
#   docker build -t wardrowbe-ui -f frontend/Dockerfile frontend
#   docker run --rm -p 3000:3000 -e BACKEND_URL=http://api.internal:8000 wardrowbe-ui
#
# BACKEND_URL is read per request by app/api/v1/[...path]/route.ts, so the
# image is host-agnostic - nothing to rebuild when the API moves.

FROM node:24-alpine AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

FROM node:24-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# No PUID/PGID remapping (and no su-exec/shadow packages): the upstream entry
# point existed for Home-Linux-style bind mounts, which this deployment has no.
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
# next-intl resolves these at request time via a dynamic import.
COPY --from=builder --chown=nextjs:nodejs /app/messages ./messages
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${PORT}/" || exit 1

CMD ["node", "server.js"]
