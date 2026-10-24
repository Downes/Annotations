# Annotations App

## Repository
- GitHub: https://github.com/Downes/Annotations

## Container
- Name: `annotations`
- Port: 8083 (internal only; Caddy proxies via Docker `web` network)
- Domain: annotations.mooc.ca

## Database
- SQLite at `/app/data/annotations.db`
- Volume-mounted from `./data/` on the host
- Schema defined in `db.py` → `init_db()`
- Never modify the database directly; use the API

## Auth
- Write endpoints require `Authorization: Bearer <token>`
- Token verification is local JWT/JWKS ("Pattern B") via the shared
  `clist-kvstore-auth` package (`clist_kvstore_auth.asyncio`), pinned by
  commit SHA in `requirements.txt` (github.com/Downes/clist-kvstore-auth),
  `verify_token()` in `auth.py` (a FastAPI dependency). A JWT's own `iss`
  claim names the instance that signed it; we fetch *that* instance's
  `/.well-known/jwks.json` and verify the signature — open to any issuer,
  no allowlist (anyone can run their own kvstore and interoperate).
- Opaque (pre-migration) tokens carry no `iss` and can't be verified
  locally, so they still fall back to `GET {KVSTORE_URL}/auth/verify`
  (`KVSTORE_URL` env var is the fallback default, overridden by
  `app_settings.KVSTORE_URL` once an admin saves one — see Admin settings
  below).
- `verify_token()` returns an `Identity(username, iss, qualified_id)`
  object, not a bare dict — `qualified_id` is
  `did:web:<kvstore-host>:users:<username>`, derived from the token's real
  `iss` (not from whichever `KVSTORE_URL` the request happened to be
  configured with, which is what this app did before the Pattern B
  migration).
- Owner enforcement: `annotations.creator` stores `identity.qualified_id`.
  No legacy bare-username fallback is needed here (unlike pastebin/survey/
  discussions) — this app's `creator`/`did` values were already
  `did:web:<host>:users:<username>`-shaped before the migration, and that
  host was always derived from this app's own `KVSTORE_URL`, so a
  pre-migration row's stored value already equals what `identity.qualified_id`
  resolves to today, as long as `KVSTORE_URL` hasn't changed since. (If an
  admin ever changes `KVSTORE_URL`, rows created under the old value become
  orphaned from their original creator's ownership — same caveat class as
  the admin-lockout risk below, not handled specially.)
- `/admin/settings` is additionally gated by a locally-reimplemented
  `is_admin(identity)` in `auth.py`, not `clist_kvstore_auth.asyncio.is_admin()`
  or `clist_server_admin.fastapi.is_admin()` directly, because `KVSTORE_URL`
  is DB-overridable here (not just a static env var) and neither shared
  package's own `is_admin()` checks the issuer, only the username — see
  Admin settings below.

## Admin settings
Generic admin-editable runtime settings — imported from the shared
`clist-server-admin` package (`clist_server_admin.fastapi`), pinned by
commit SHA in `requirements.txt` (github.com/Downes/clist-server-admin,
`[fastapi]` extra). No local `admin_settings.py` copy anymore.
`ADMIN_USERNAMES`
(`docker-compose.yml` environment block, comma-separated — this app has no
`.env` file, env vars are set directly in compose) gates `/admin/settings`
(Bearer token + username in that set, else 403). Two fields: `ENABLED`
(boolean, default `true`) and `KVSTORE_URL` (non-sensitive, url).

`ENABLED` is a kill switch, implemented as a second `@app.middleware("http")`
function (`_enabled_gate`, alongside the existing `CORSMiddleware`) that
returns `503 {"error": "Service temporarily disabled by administrator"}`
for every path except `/admin/settings` and `/health` when set to `false`.

This variant differs from the Flask pilots (pastebin/survey/localrss_reader)
because this app is FastAPI + aiosqlite, not Flask + sqlite3:
- All settings-table access is async, opening a fresh `aiosqlite` connection
  per call via a `db_path` string — matching this app's own `db.py` style
  (no cached connection object, no `get_db()`).
- Admin gating is a FastAPI dependency, not a zero-arg `verify_token()`
  returning `(username, err)`. `admin_dependency` in `auth.py` wraps the
  app's `verify_token` dependency with the locally-reimplemented,
  issuer-aware `is_admin(identity)` (not
  `clist_server_admin.fastapi.make_admin_dependency()`, whose own
  `is_admin()` only checks username, not issuer), so routes just use
  `Depends(admin_dependency)` like every other route here uses
  `Depends(verify_token)`.

**Caveat**: `KVSTORE_URL` controls which kvstore authenticates every request
on this app, including the admin's own token. A bad value locks the admin
out of `/admin/settings` itself until reverted directly in the
`app_settings` row of `data/annotations.db` (`sqlite3 data/annotations.db
"UPDATE app_settings SET value='https://kvstore.mooc.ca' WHERE
key='KVSTORE_URL'"`).

## Rebuild
```bash
cd /srv/apps/annotations
docker compose build && docker compose up -d
docker compose logs -f annotations
```
Never rebuild without stating the exact command, confirming no data loss,
and receiving explicit user confirmation.

## Key Files
- `app.py` — FastAPI routes (all endpoints)
- `db.py` — SQLite async queries
- `auth.py` — KVStore token verification
- `w3c.py` — W3C JSON-LD serialization
- `models.py` — Pydantic request/response schemas
