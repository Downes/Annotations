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
- Token verified via `GET https://kvstore.mooc.ca/auth/verify`
- KVStore returns `{"did": "...", "username": "..."}` on success

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
