# Removed vs retained

Everything below was decided by tracing usage, not by filename. "Removed" means the code, its
routes, its env vars and its UI are gone; "retained" means the original behaviour survives with
less machinery around it.

## Removed

| Component | Original shape | Cost it was carrying |
|---|---|---|
| Redis / Key Value | queue broker for arq | a second always-on service |
| arq task queue | `worker.py`, retry/backoff, job ids | a second always-on service |
| Image worker | `image_worker.py` | a third service, idle 99 % of the time |
| Scheduled notifications | `apscheduler` morning/evening cron, per-user timezone math | an always-on loop |
| ntfy push | device tokens, per-user endpoints | — |
| Mattermost webhook | team digests | — |
| SMTP email | templated outfit digests | an SMTP host on a free tier: none |
| OIDC / Keycloak | full SSO, tokens, user sync | a fourth service |
| NextAuth (frontend) | session provider, middleware, `useToken` | — |
| Family & sharing | invites, pairs, voting, "who wore what" tables | 5+ tables, ~10 endpoints |
| Advanced analytics | per-user dashboards, aggregations | — |
| Learning engine | preference weights fed by accept/reject history | background recompute |
| Duplicate detection | pHash + threshold | an image dep for an unexercised feature |
| Background removal | `rembg` + `u2net` weights downloaded at build time | ~500 MB image, ~40 s build |
| Image rotation / galleries | per-item multi-photo ops | worker-only paths |
| Wash log | `wash_log` rows + UI | not part of this flow |
| IP geolocation fallback | "privacy-preserving" city lookup | an external call, wrong results |
| K8s manifests, Nginx/Caddy config | multi-service topology, TLS, routing | — |
| Alembic migrations | schema versions for 20+ tables | — |
| S3-only-when-configured extras | presigned redirects kept, bucket policy docs dropped | — |
| `sharp` (frontend dep) | Next image optimization | fails to install on ARM64/some CI |
| Locales `de es fr hi mr ta te` | 8 UI languages | 7 × the strings to keep in sync |

## Retained (behaviour identical, plumbing gone)

| Capability | Where it lives now |
|---|---|
| Clothing analysis → structured JSON (`type`, `subtype`, `primary_color`, `additional_colors`, `pattern`, `material`, `formality`, `style[]`, `season[]`, `fit`, `condition`, `notes`, `completeness`, `confidence`) | `backend/app/ai_service.py` (`ClothingTags`) + `deps.apply_tags` |
| Outfit generation: ~3 looks with `name`/headline, `reasoning`, `highlights[]`, `style_notes`, ordered `items[]`, weather + palette context | `backend/app/outfit_service.py` + `api/outfits.py` |
| Bulk upload with per-file partial failure | `POST /api/v1/items/bulk` |
| Progress polling the existing UI expects | `GET /api/v1/items/tagging-progress` |
| Manual edit + re-analyze of an item | `PATCH /api/v1/items/{id}`, `POST /api/v1/items/{id}/analyze` |
| Image replace | `PUT /api/v1/items/{id}/image` |
| Archive (not delete) | `PATCH /api/v1/items/{id}` with `is_archived` |
| Accept / reject / feedback / skip an outfit | `/api/v1/outfits/{id}/{accept,reject,feedback,skip}` |
| Thumbnail + medium renditions | `storage.py` + `GET /api/v1/images/{key}` |
| Weather-aware suggestions (Open-Meteo, key-free) | `misc.py`, `weather_enabled` |
| Location consent UI (reduced to lat/lon) | `use-update-location` → `POST /users/me/location` |
| AI-reachability self-check from the browser | `GET /api/v1/capabilities` |
| The whole look & feel: layout, components, Tailwind theme, animations | `frontend/` unchanged apart from removed pages/links |

## Frontend: removed vs kept

Removed: `components/auth/` (NextAuth `AuthProvider`, `user-info`, `auth-test`,
`auth-debug-panel`), `middleware.ts` (session gate + security headers), `app/(auth)/login`,
`app/privacy`, `app/terms`, `app/api/auth/[...nextauth]`, `components/providers/` (the
family/pairings context), `family-picker.tsx`, `share-dialog.tsx`, `outfit-share-action.tsx`,
`share-image-modal.tsx`, `wash-log-modal.tsx`, `settings/family-section.tsx`, the AI-debug drawer,
the 7 extra locales, and the deps behind them (`next-auth`, `react-hook-form`, `zod`, `zustand`,
`sharp`) — `tailwindcss-animate` is still used by `tailwind.config.js`, so it stays.

Kept, with the dead calls cut out of them: the dashboard shell and sidebar, the full wardrobe grid
+ filters + item card + detail sheet, the suggest flow (`GenerateOutfitsCard` →
`OutfitDisplay` → `useAcceptOutfit`), the outfit history + detail pages, and the settings page
(reduced to profile / location / preferences / data-and-limits).

`lib/hooks/use-auth.ts` is now a session-free shim that returns the implicit demo user. It also
documents the re-enable path (restore `middleware.ts`, the `[...nextauth]` route, and this file's
`signIn` call) for when auth comes back. `components/locale-switcher.tsx` is pinned to English for
the same reason, with the restore instructions in its comment.

## API surface (31 paths, was ~90)

```
GET  /api/v1/auth/status            GET  /api/v1/capabilities
POST /api/v1/auth/sync              GET  /api/v1/health{,/features,/ready,/storage}

GET  /api/v1/images/{key}           GET  /api/v1/images/redirect/{key}
GET  /api/v1/items                  POST /api/v1/items
POST /api/v1/items/bulk             POST /api/v1/items/bulk/delete
GET  /api/v1/items/tagging-progress GET  /api/v1/items/types
GET|PATCH|DELETE /api/v1/items/{item_id}
POST /api/v1/items/{item_id}/analyze
GET|PUT /api/v1/items/{item_id}/image

GET  /api/v1/outfits                GET  /api/v1/outfits/pending
POST /api/v1/outfits/suggest        POST /api/v1/outfits/suggest-options
GET|DELETE /api/v1/outfits/{outfit_id}
POST /api/v1/outfits/{outfit_id}/{accept,reject,feedback,skip}

GET|PATCH /api/v1/users/me          POST /api/v1/users/me/location
POST /api/v1/users/me/onboarding/complete
GET  /api/v1/weather/current        POST /api/v1/weather/preview
GET  /healthz                       (root, liveness, no DB/AI probe)
```

## Database: 4 tables, portable types

`User` (implicit demo row, kept so `user_id` foreign keys stay meaningful), `ClothingItem`,
`Outfit`, `OutfitItem`. All SQLAlchemy `Base` types only — `String(36)` uuids generated in Python,
`Text` for JSON payloads, `Float`, `DateTime(timezone=True)` with Python-side `server_default` —
so the same schema runs on SQLite and Postgres without a dialect branch. `JSONB`, `ARRAY`,
`postgresql.ENUM`, `server_default=func.now()` and `interval` arithmetic (all used upstream) are
gone; the places that needed them do the work in Python now.
