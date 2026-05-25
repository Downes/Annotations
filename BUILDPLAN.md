# CList Federated Annotation Service — Build Plan

**Target:** Deploy a federated annotation service for CList on VPS 158.69.209.43  
**Domain:** `annotations.mooc.ca`  
**Container port:** 8083  
**Written for:** Remote Claude agent

---

## Read These First

Before touching anything, read:
- `/srv/CLAUDE.md` — Server-wide conventions and permissions model
- `/srv/apps/kvstore/CLAUDE.md` — KVStore auth API details
- `/srv/proxy/Caddyfile` — Current Caddy routing (to find safe insertion point)

Key permissions rule: **never restart or rebuild a container without (a) stating the exact command, (b) confirming no data will be lost, (c) receiving explicit user confirmation.**

---

## Architecture Overview

### What This Service Is

A web server that stores and serves annotations — notes, highlights, and comments that users attach to URLs. It is the backend for CList's annotation feature.

### What It Is Not

- Not a replacement for KVStore (identity/auth still lives there)
- Not a federation hub (CList browser code does the aggregation, not this service)
- Not a push/sync system (pure pull: whoever wants your annotations comes to you)

### How Federation Works

```
User views https://example.com/article in CList
        │
        ▼
CList (JS) reads user's followed-DIDs list from KVStore
        │
        ▼ for each followed DID:
CList resolves DID doc → finds "AnnotationStore" service endpoint
        │
        ▼
CList queries: GET {endpoint}/annotations?target=https%3A%2F%2Fexample.com%2Farticle
        │
        ▼
CList aggregates + renders all annotations inline on the page
```

The annotation service just has to respond to these queries. CList does the fan-out.

### Design Principles (from source documents)

1. **Follow people, not servers** — federation follows the social graph, not server topology
2. **DID-based identity** — annotations belong to their creator's DID; server is custodian only. **Important:** CList does not require users to create DIDs. Any kvstore user can create annotations; the service derives a creator identifier (`did:web:{kvstore_domain}:users:{username}`) from their kvstore token. A published DID document is only needed for *federation* — other CList instances discovering your annotation store by resolving your DID. Without a published DID, your annotations are still stored and visible locally.
3. **W3C Web Annotation Data Model** — external API is W3C-compliant JSON-LD (interoperable with Hypothesis, etc.)
4. **Separate from KVStore** — annotation service owns its own database and container
5. **Pull-based** — no push, no sync; remote stores are queried on demand

---

## Technology Stack

| Component | Choice | Reason |
|-----------|--------|--------|
| Language | Python 3.11 | Consistent with kvstore, opml2json |
| Framework | FastAPI | Async, automatic OpenAPI docs, same as opml2json |
| Server | Uvicorn | Standard for FastAPI |
| Database | SQLite + aiosqlite | Simple, no sidecar needed; data volume is low |
| Container | Docker + docker-compose | VPS convention |
| Reverse proxy | Caddy (existing) | Add one stanza to existing Caddyfile |

---

## Directory Structure

```
/srv/apps/annotations/
├── BUILDPLAN.md            ← this file
├── CLAUDE.md               ← create this (template at bottom of document)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── app.py                  ← FastAPI application + routes
├── db.py                   ← Database init + query functions
├── auth.py                 ← KVStore token verification
├── models.py               ← Pydantic request/response schemas
├── w3c.py                  ← W3C JSON-LD serialization helpers
└── data/                   ← volume-mounted; created at startup
    └── annotations.db      ← SQLite (auto-created)
```

---

## Data Model

### SQLite Schema (`db.py`)

```sql
CREATE TABLE IF NOT EXISTS annotations (
    id              TEXT PRIMARY KEY,
    creator         TEXT NOT NULL,       -- DID, e.g. did:web:kvstore.mooc.ca:users:stephen
    target_url      TEXT NOT NULL,       -- URL being annotated
    target_selector TEXT,                -- JSON: W3C selector (text quote, range, etc.)
    body            TEXT NOT NULL,       -- Annotation text (markdown allowed)
    tags            TEXT DEFAULT '[]',   -- JSON array of strings
    created         TEXT NOT NULL,       -- ISO 8601 UTC, e.g. 2026-05-15T12:00:00Z
    modified        TEXT,                -- ISO 8601 UTC, null until edited
    visibility      TEXT DEFAULT 'public',  -- 'public' | 'private' | 'followers'
    motivation      TEXT DEFAULT 'commenting', -- W3C motivation vocabulary
    proof           TEXT                 -- JSON: Ed25519 signature (nullable; future use)
);

CREATE INDEX IF NOT EXISTS idx_target  ON annotations(target_url);
CREATE INDEX IF NOT EXISTS idx_creator ON annotations(creator);
CREATE INDEX IF NOT EXISTS idx_created ON annotations(created);
```

### W3C JSON-LD Output Format

Single annotation:
```json
{
  "@context": "http://www.w3.org/ns/anno.jsonld",
  "id": "https://annotations.mooc.ca/annotations/550e8400-e29b-41d4-a716-446655440000",
  "type": "Annotation",
  "motivation": "commenting",
  "creator": {
    "id": "did:web:kvstore.mooc.ca:users:stephen",
    "type": "Person"
  },
  "created": "2026-05-15T12:00:00Z",
  "modified": null,
  "body": {
    "type": "TextualBody",
    "value": "This is an interesting point about federated learning.",
    "format": "text/markdown"
  },
  "target": {
    "source": "https://example.com/some/article",
    "selector": {
      "type": "TextQuoteSelector",
      "exact": "interesting point",
      "prefix": "This is an ",
      "suffix": " about federated"
    }
  }
}
```

AnnotationPage (collection response):
```json
{
  "@context": "http://www.w3.org/ns/anno.jsonld",
  "type": "AnnotationPage",
  "startIndex": 0,
  "total": 42,
  "items": [ ... ]
}
```

---

## API Endpoints

### Public (no auth required)

#### `GET /health`
```json
{"status": "ok", "version": "1.0.0"}
```

#### `GET /.well-known/annotation-service`
Service discovery — CList uses this to identify annotation stores.
```json
{
  "type": "AnnotationStore",
  "endpoint": "https://annotations.mooc.ca",
  "api": "https://annotations.mooc.ca/annotations",
  "feed": "https://annotations.mooc.ca/annotations/feed",
  "creator_query": "https://annotations.mooc.ca/annotations?creator={did}",
  "target_query": "https://annotations.mooc.ca/annotations?target={url}"
}
```

#### `GET /annotations`
Query annotations. At least one of `target` or `creator` should be provided; without either, returns recent public annotations.

Query parameters:
| Param | Type | Description |
|-------|------|-------------|
| `target` | str | URL-encoded target URL |
| `creator` | str | Creator DID |
| `since` | str | ISO 8601 timestamp; only return annotations created/modified after this |
| `limit` | int | Default 50, max 200 |
| `offset` | int | Default 0 |

Visibility filtering:
- Unauthenticated: return only `visibility=public` annotations
- Authenticated as the creator: return all visibilities
- Authenticated as other: return `public` and `followers` (followers check is future work; treat `followers` as public for now)

Response: `200 OK`, W3C AnnotationPage JSON-LD

#### `GET /annotations/{id}`
Single annotation by UUID. Returns `404` if not found or `private` (for non-creator).

Response: `200 OK`, W3C Annotation JSON-LD

#### `GET /annotations/feed`
Atom feed of recent public annotations.

Query parameters:
| Param | Type | Description |
|-------|------|-------------|
| `creator` | str | Filter by creator DID |
| `target` | str | Filter by target URL |

Response: `200 OK`, `Content-Type: application/atom+xml`

### Authenticated (require `Authorization: Bearer <token>`)

Tokens are validated against KVStore at `GET https://kvstore.mooc.ca/auth/verify`.  
KVStore returns `{"did": "did:web:...", "username": "..."}` on success.

#### `POST /annotations`
Create a new annotation.

Request body:
```json
{
  "target_url": "https://example.com/article",
  "target_selector": {
    "type": "TextQuoteSelector",
    "exact": "exact quoted text",
    "prefix": "text before",
    "suffix": "text after"
  },
  "body": "My annotation (markdown ok)",
  "tags": ["education", "OER"],
  "visibility": "public",
  "motivation": "commenting"
}
```

`target_selector` is optional (null for page-level annotations).  
`id` and `created` are generated by the server; do not accept them from the client.

Response: `201 Created`, full W3C Annotation JSON-LD

#### `PUT /annotations/{id}`
Update an annotation. Only the annotation's `creator` may update it (check `creator == user["did"]`).

Request body: same fields as POST, all optional. Only provided fields are updated; `modified` is set to now.

Response: `200 OK`, updated W3C Annotation JSON-LD  
Error: `403 Forbidden` if requester is not the creator

#### `DELETE /annotations/{id}`
Delete an annotation. Creator only.

Response: `204 No Content`  
Error: `403 Forbidden` if requester is not the creator

---

## File Implementations

### `requirements.txt`

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
aiosqlite==0.20.0
httpx==0.27.2
pydantic==2.9.0
python-multipart==0.0.12
feedgen==1.0.0
```

### `Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8083", "--log-level", "info"]
```

### `docker-compose.yml`

```yaml
services:
  annotations:
    build: .
    container_name: annotations
    restart: unless-stopped
    volumes:
      - ./data:/app/data
    environment:
      - KVSTORE_URL=https://kvstore.mooc.ca
      - DATABASE_URL=/app/data/annotations.db
      - BASE_URL=https://annotations.mooc.ca
    networks:
      - web

networks:
  web:
    external: true
```

Note: no `ports:` mapping needed — Caddy reaches the container via the `web` Docker network by container name.

### `models.py`

```python
from pydantic import BaseModel, field_validator
from typing import Optional, Any

class AnnotationCreate(BaseModel):
    target_url: str
    target_selector: Optional[Any] = None  # free-form JSON
    body: str
    tags: list[str] = []
    visibility: str = "public"
    motivation: str = "commenting"

    @field_validator("visibility")
    @classmethod
    def check_visibility(cls, v):
        if v not in ("public", "private", "followers"):
            raise ValueError("visibility must be public, private, or followers")
        return v

    @field_validator("motivation")
    @classmethod
    def check_motivation(cls, v):
        allowed = {"commenting", "bookmarking", "classifying", "describing",
                   "editing", "highlighting", "identifying", "linking",
                   "moderating", "questioning", "replying", "tagging"}
        if v not in allowed:
            raise ValueError(f"motivation must be one of: {', '.join(sorted(allowed))}")
        return v


class AnnotationUpdate(BaseModel):
    target_selector: Optional[Any] = None
    body: Optional[str] = None
    tags: Optional[list[str]] = None
    visibility: Optional[str] = None
    motivation: Optional[str] = None

    @field_validator("visibility")
    @classmethod
    def check_visibility(cls, v):
        if v is not None and v not in ("public", "private", "followers"):
            raise ValueError("visibility must be public, private, or followers")
        return v
```

### `db.py`

```python
import aiosqlite
import json
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "/app/data/annotations.db")


async def init_db():
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS annotations (
                id              TEXT PRIMARY KEY,
                creator         TEXT NOT NULL,
                target_url      TEXT NOT NULL,
                target_selector TEXT,
                body            TEXT NOT NULL,
                tags            TEXT DEFAULT '[]',
                created         TEXT NOT NULL,
                modified        TEXT,
                visibility      TEXT DEFAULT 'public',
                motivation      TEXT DEFAULT 'commenting',
                proof           TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_target  ON annotations(target_url)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_creator ON annotations(creator)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_created ON annotations(created)")
        await db.commit()


async def get_annotation(annotation_id: str) -> dict | None:
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM annotations WHERE id = ?", (annotation_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def query_annotations(
    target_url: str | None,
    creator: str | None,
    since: str | None,
    visibility_filter: list[str],
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    conditions = [f"visibility IN ({','.join('?' * len(visibility_filter))})"]
    params = list(visibility_filter)

    if target_url:
        conditions.append("target_url = ?")
        params.append(target_url)
    if creator:
        conditions.append("creator = ?")
        params.append(creator)
    if since:
        conditions.append("(created > ? OR (modified IS NOT NULL AND modified > ?))")
        params.extend([since, since])

    where = " AND ".join(conditions)

    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            f"SELECT COUNT(*) FROM annotations WHERE {where}", params
        ) as cursor:
            total = (await cursor.fetchone())[0]

        async with db.execute(
            f"SELECT * FROM annotations WHERE {where} ORDER BY created DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]

    return rows, total


async def insert_annotation(row: dict):
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            """INSERT INTO annotations
               (id, creator, target_url, target_selector, body, tags, created,
                modified, visibility, motivation, proof)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"], row["creator"], row["target_url"],
                row.get("target_selector"), row["body"],
                json.dumps(row.get("tags", [])), row["created"],
                None, row["visibility"], row["motivation"], None,
            ),
        )
        await db.commit()


async def update_annotation(annotation_id: str, updates: dict):
    fields = []
    params = []
    for key in ("target_selector", "body", "tags", "visibility", "motivation"):
        if key in updates and updates[key] is not None:
            fields.append(f"{key} = ?")
            val = json.dumps(updates[key]) if key == "tags" else updates[key]
            params.append(val)
    if not fields:
        return
    fields.append("modified = ?")
    params.append(updates["modified"])
    params.append(annotation_id)

    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            f"UPDATE annotations SET {', '.join(fields)} WHERE id = ?", params
        )
        await db.commit()


async def delete_annotation(annotation_id: str):
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
        await db.commit()
```

### `auth.py`

```python
import os
import httpx
from fastapi import HTTPException, Header
from typing import Optional

KVSTORE_URL = os.environ.get("KVSTORE_URL", "https://kvstore.mooc.ca")


async def verify_token(authorization: Optional[str] = Header(None)) -> dict:
    """Verify Bearer token against KVStore. Returns {"did": ..., "username": ...}."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{KVSTORE_URL}/auth/verify",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"KVStore unreachable: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return resp.json()
```

### `w3c.py`

```python
import json
import os

BASE_URL = os.environ.get("BASE_URL", "https://annotations.mooc.ca")


def to_w3c_annotation(row: dict) -> dict:
    """Convert a database row dict to W3C Web Annotation JSON-LD."""
    tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"]
    selector = json.loads(row["target_selector"]) if row.get("target_selector") else None

    anno = {
        "@context": "http://www.w3.org/ns/anno.jsonld",
        "id": f"{BASE_URL}/annotations/{row['id']}",
        "type": "Annotation",
        "motivation": row["motivation"],
        "creator": {
            "id": row["creator"],
            "type": "Person",
        },
        "created": row["created"],
        "modified": row.get("modified"),
        "body": {
            "type": "TextualBody",
            "value": row["body"],
            "format": "text/markdown",
        },
        "target": {
            "source": row["target_url"],
            "selector": selector,
        },
    }

    if tags:
        anno["tag"] = tags

    return anno


def to_w3c_page(rows: list[dict], total: int, offset: int) -> dict:
    """Wrap a list of annotations in a W3C AnnotationPage."""
    return {
        "@context": "http://www.w3.org/ns/anno.jsonld",
        "type": "AnnotationPage",
        "startIndex": offset,
        "total": total,
        "items": [to_w3c_annotation(r) for r in rows],
    }
```

### `app.py`

```python
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from feedgen.feed import FeedGenerator

from auth import verify_token
from db import (
    delete_annotation, get_annotation, init_db,
    insert_annotation, query_annotations, update_annotation,
)
from models import AnnotationCreate, AnnotationUpdate
from w3c import BASE_URL, to_w3c_annotation, to_w3c_page


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="CList Annotation Service", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


# ── Service discovery ─────────────────────────────────────────────────────────

@app.get("/.well-known/annotation-service")
async def service_discovery():
    return {
        "type": "AnnotationStore",
        "endpoint": BASE_URL,
        "api": f"{BASE_URL}/annotations",
        "feed": f"{BASE_URL}/annotations/feed",
        "creator_query": f"{BASE_URL}/annotations?creator={{did}}",
        "target_query": f"{BASE_URL}/annotations?target={{url}}",
    }


# ── Read endpoints ────────────────────────────────────────────────────────────

@app.get("/annotations/feed")
async def annotation_feed(
    creator: Optional[str] = Query(None),
    target: Optional[str] = Query(None),
):
    rows, _ = await query_annotations(
        target_url=target,
        creator=creator,
        since=None,
        visibility_filter=["public"],
        limit=50,
        offset=0,
    )

    fg = FeedGenerator()
    fg.id(f"{BASE_URL}/annotations/feed")
    fg.title("CList Annotations")
    fg.link(href=f"{BASE_URL}/annotations/feed", rel="self")
    fg.language("en")

    for row in rows:
        fe = fg.add_entry()
        fe.id(f"{BASE_URL}/annotations/{row['id']}")
        fe.title(f"Annotation on {row['target_url']}")
        fe.content(row["body"], type="text")
        fe.published(row["created"])
        fe.link(href=row["target_url"])

    return Response(content=fg.atom_str(pretty=True), media_type="application/atom+xml")


@app.get("/annotations/{annotation_id}")
async def get_one(annotation_id: str):
    row = await get_annotation(annotation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Annotation not found")
    if row["visibility"] == "private":
        raise HTTPException(status_code=404, detail="Annotation not found")
    return to_w3c_annotation(row)


@app.get("/annotations")
async def list_annotations(
    target: Optional[str] = Query(None),
    creator: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    rows, total = await query_annotations(
        target_url=target,
        creator=creator,
        since=since,
        visibility_filter=["public", "followers"],
        limit=limit,
        offset=offset,
    )
    return to_w3c_page(rows, total, offset)


# ── Write endpoints ───────────────────────────────────────────────────────────

@app.post("/annotations", status_code=201)
async def create_annotation(
    body: AnnotationCreate,
    user: dict = Depends(verify_token),
):
    row = {
        "id": str(uuid.uuid4()),
        "creator": user["did"],
        "target_url": body.target_url,
        "target_selector": json.dumps(body.target_selector) if body.target_selector else None,
        "body": body.body,
        "tags": body.tags,
        "created": now_iso(),
        "visibility": body.visibility,
        "motivation": body.motivation,
    }
    await insert_annotation(row)
    saved = await get_annotation(row["id"])
    return to_w3c_annotation(saved)


@app.put("/annotations/{annotation_id}")
async def update_one(
    annotation_id: str,
    body: AnnotationUpdate,
    user: dict = Depends(verify_token),
):
    row = await get_annotation(annotation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Annotation not found")
    if row["creator"] != user["did"]:
        raise HTTPException(status_code=403, detail="Not your annotation")

    updates = body.model_dump(exclude_none=True)
    updates["modified"] = now_iso()
    await update_annotation(annotation_id, updates)
    saved = await get_annotation(annotation_id)
    return to_w3c_annotation(saved)


@app.delete("/annotations/{annotation_id}", status_code=204)
async def delete_one(
    annotation_id: str,
    user: dict = Depends(verify_token),
):
    row = await get_annotation(annotation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Annotation not found")
    if row["creator"] != user["did"]:
        raise HTTPException(status_code=403, detail="Not your annotation")
    await delete_annotation(annotation_id)
```

---

## Caddyfile Entry

Add this block to `/srv/proxy/Caddyfile`, in the same style as other apps:

```caddyfile
annotations.mooc.ca {
    handle /.well-known/acme-challenge/* {
        root * /srv/www/annotations.mooc.ca
        file_server
    }

    reverse_proxy annotations:8083
}
```

Create the ACME challenge directory before reloading Caddy:
```bash
mkdir -p /srv/www/annotations.mooc.ca
```

**Always validate before reloading:**
```bash
# From /srv/proxy:
docker compose exec caddy caddy validate --config /dev/stdin --adapter caddyfile < Caddyfile

# If valid:
docker compose exec -T caddy caddy reload --config /dev/stdin --adapter caddyfile < Caddyfile
```

A syntax error in the Caddyfile takes down all hosted sites. Do not skip validation.

---

## DNS

Add an A record for `annotations.mooc.ca` pointing to `158.69.209.43`. Caddy will provision the TLS certificate automatically via ACME once DNS propagates.

---

## Implementation Order

Follow this sequence. Stop and check container logs after each docker step.

### Step 1 — Write all source files

Create the following files in `/srv/apps/annotations/`:
- `requirements.txt`
- `Dockerfile`
- `docker-compose.yml`
- `models.py`
- `db.py`
- `auth.py`
- `w3c.py`
- `app.py`
- `CLAUDE.md` (see template below)

Use the full implementations from this document.

### Step 2 — Build container

```bash
cd /srv/apps/annotations
docker compose build
```

Check for build errors. Common issues: pip install fails (network), missing package version.

### Step 3 — Start container

Show the user the exact command before running:
```bash
docker compose up -d
docker compose logs -f annotations
```

Confirm `Application startup complete` appears in logs.

### Step 4 — Test locally (from VPS)

```bash
curl http://localhost:8083/health
# Expected: {"status":"ok","version":"1.0.0"}

curl http://localhost:8083/.well-known/annotation-service
# Expected: JSON with type, endpoint, api, feed fields

curl "http://localhost:8083/annotations?target=https://example.com"
# Expected: {"type":"AnnotationPage","total":0,"items":[]}
```

### Step 5 — Add DNS record

Add `annotations.mooc.ca` → `158.69.209.43` at the DNS provider. Wait for propagation (check with `dig annotations.mooc.ca`).

### Step 6 — Add Caddy entry

1. Create ACME directory: `mkdir -p /srv/www/annotations.mooc.ca`
2. Edit `/srv/proxy/Caddyfile` — add the block from the "Caddyfile Entry" section above
3. Validate
4. Reload (via stdin pipe — see command above)

### Step 7 — Test via HTTPS

```bash
curl https://annotations.mooc.ca/health
curl https://annotations.mooc.ca/.well-known/annotation-service
```

### Step 8 — Test authenticated write

```bash
# Get a token from KVStore first (manual step — user provides token)
TOKEN=<user's token>

# Create an annotation
curl -X POST https://annotations.mooc.ca/annotations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "target_url": "https://www.downes.ca/post/1234",
    "body": "Test annotation from build plan.",
    "tags": ["test"],
    "visibility": "public"
  }'
# Expected: 201 Created with W3C JSON-LD annotation including generated id

# Fetch it back
curl "https://annotations.mooc.ca/annotations?target=https%3A%2F%2Fwww.downes.ca%2Fpost%2F1234"
# Expected: AnnotationPage with 1 item
```

### Step 9 — Write CLAUDE.md

Fill in the CLAUDE.md template below and save it as `/srv/apps/annotations/CLAUDE.md`.

### Step 10 — Update server docs

Update `/srv/CLAUDE.md` key services table to add the annotations row.  
Update `/srv/shared/setup-notes.md` with the new service.

---

## CLAUDE.md Template (create this file)

```markdown
# Annotations App

## Container
- Name: `annotations`
- Port: 8083 (internal only; Caddy proxies via Docker network)
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
**Never rebuild without stating the exact command, confirming no data loss,
and receiving explicit user confirmation.**

## Key Files
- `app.py` — FastAPI routes (all endpoints)
- `db.py` — SQLite async queries
- `auth.py` — KVStore token verification
- `w3c.py` — W3C JSON-LD serialization
- `models.py` — Pydantic request/response schemas
```

---

## Service Discovery Integration with KVStore DID Profiles

After the service is running, users who want to publish annotations in their DID document should add this to their KVStore profile (exact KVStore API endpoint — verify with `/srv/apps/kvstore/CLAUDE.md`):

```json
{
  "service": [
    {
      "id": "#annotation-store",
      "type": "AnnotationStore",
      "serviceEndpoint": "https://annotations.mooc.ca"
    }
  ]
}
```

CList resolves DID documents from KVStore and looks for the `AnnotationStore` service endpoint. The annotation service does not need to do anything special to support this — CList handles the lookup.

---

## Future Work (not in this build)

- **Signature verification** — `proof` field exists in the schema; Ed25519 signing/verification deferred
- **Followers-only visibility** — Currently treated as public; proper followers check requires KVStore social graph API
- **Import adapters** — Hypothesis (`https://hypothes.is/api/search?uri={url}`), Obsidian vault format
- **Text highlight display** — CList JS to render `TextQuoteSelector` highlights in-page
- **Pagination UI** — CList JS handling of `total` and `offset` for large annotation sets
- **Annotate from the editor** — Any content open in CList's editing window should be annotatable: selecting text (or the whole draft) and invoking "Annotate" would pre-populate the annotation form with the selected content as the body and the draft's URL (or a `did:` URI for unpublished drafts) as the target. Requires a bridge between the editor and `showAnnotations` / `_submitAnnotation`.
- **Multi-target annotations** — An annotation should be attachable to more than one URL (e.g., a note that applies equally to the original post and a syndicated copy, or a comparison note spanning two resources). Design options: (a) store `target_url` as a JSON array and update the schema, query index, and W3C serializer to emit a `target` array; (b) store one row per target and group by a shared `annotation_group_id`; option (a) is simpler and maps cleanly to the W3C model, which already allows `target` to be an array.

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `KVSTORE_URL` | `https://kvstore.mooc.ca` | KVStore base URL for auth verification |
| `DATABASE_URL` | `/app/data/annotations.db` | SQLite file path inside container |
| `BASE_URL` | `https://annotations.mooc.ca` | Public base URL (used in annotation `id` fields) |
