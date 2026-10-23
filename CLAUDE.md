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
- Token verified via `GET {KVSTORE_URL}/auth/verify` — `KVSTORE_URL` env var is the
  fallback default (`https://kvstore.mooc.ca`), overridden by `app_settings.KVSTORE_URL`
  once an admin saves one (see Admin settings below)
- KVStore returns `{"did": "...", "username": "..."}` on success

## Admin settings
Generic admin-editable runtime settings — FastAPI/aiosqlite variant, see
`/srv/shared/admin_settings_fastapi.py` (reference copy; this app's own
`admin_settings.py` is a verbatim copy, not an import). `ADMIN_USERNAMES`
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
  returning `(username, err)`. `make_admin_dependency(verify_token)` wraps
  the app's existing `verify_token` dependency with an `is_admin()` check,
  so routes just use `Depends(admin_dep)` like every other route here uses
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
