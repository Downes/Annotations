"""
CList annotation service — W3C-ish annotation storage with a kvstore
identity layer on top.

Write endpoints require a valid kvstore Bearer token, verified locally via
JWT/JWKS ("Pattern B") against the issuer named in the token's own `iss`
claim — open to any kvstore instance, no allowlist. Pre-migration opaque
tokens fall back to KVSTORE_URL/auth/verify.
"""

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from feedgen.feed import FeedGenerator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from clist_kvstore_auth.asyncio import Identity
from clist_server_admin.fastapi import (
    SettingField,
    get_setting,
    init_settings_table,
    register_admin_settings_routes,
)
from auth import KVSTORE_URL_DEFAULT, admin_dependency, verify_token
from db import (
    DATABASE_URL,
    batch_check_annotations,
    delete_annotation,
    get_annotation,
    get_user,
    init_db,
    insert_annotation,
    query_annotations,
    register_user,
    update_annotation,
)
from models import AnnotationCreate, AnnotationUpdate, BatchCheckRequest
from w3c import BASE_URL, to_w3c_annotation, to_w3c_page


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_settings_table(DATABASE_URL)
    yield


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="CList Annotation Service", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


REQUIRE_READ_TOKEN = os.environ.get("REQUIRE_READ_TOKEN", "").lower() in ("1", "true", "yes")

SETTINGS_SCHEMA = [
    SettingField(
        "ENABLED", "Enabled", "boolean", False, None,
        "When off, this app's functional endpoints return 503. /admin/settings "
        "and /health stay reachable so you can turn it back on.",
    ),
    SettingField(
        "KVSTORE_URL", "KVStore URL", "url", False, None,
        "This app's own kvstore instance — used as the fallback target for "
        "pre-migration opaque tokens (GET {KVSTORE_URL}/auth/verify) and as "
        "the sole trusted issuer for ADMIN_USERNAMES admin access. Does NOT "
        "gate ordinary token verification, which is local JWT/JWKS and open "
        "to any issuer. A bad value can lock the admin out of /admin/settings "
        "until reverted directly in the app_settings row of annotations.db.",
    ),
]
SETTINGS_DEFAULTS = {"ENABLED": "true", "KVSTORE_URL": KVSTORE_URL_DEFAULT}


@app.middleware("http")
async def _enabled_gate(request: Request, call_next):
    if request.url.path in ("/admin/settings", "/health"):
        return await call_next(request)
    if await get_setting(DATABASE_URL, "ENABLED", default="true") == "false":
        return JSONResponse({"error": "Service temporarily disabled by administrator"}, status_code=503)
    return await call_next(request)

register_admin_settings_routes(
    app, SETTINGS_SCHEMA, DATABASE_URL, admin_dependency, SETTINGS_DEFAULTS
)


async def optional_auth(authorization: Optional[str] = Header(None)) -> Optional[Identity]:
    """Require a valid token for reads only when REQUIRE_READ_TOKEN env var is set."""
    if not REQUIRE_READ_TOKEN:
        return None
    return await verify_token(authorization)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


# ── Registration ───────────────────────────────────────────────────────────────

@app.post("/api/register")
@limiter.limit("10/hour")
async def register(request: Request, identity: Identity = Depends(verify_token)):
    """Register the authenticated user on this annotation server."""
    result = await register_user(identity.username, identity.qualified_id)
    return {"username": result["username"], "did": result["did"]}


# ── Service discovery ──────────────────────────────────────────────────────────

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


# ── Feed ───────────────────────────────────────────────────────────────────────

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
        if row["target_url"].startswith(("http://", "https://")):
            fe.link(href=row["target_url"])

    return Response(content=fg.atom_str(pretty=True), media_type="application/atom+xml")


# ── Batch check ────────────────────────────────────────────────────────────────

@app.post("/annotations/batch-check")
@limiter.limit("60/minute")
async def batch_check(request: Request, body: BatchCheckRequest):
    """Return annotation counts for a list of URLs (public/followers only, no auth)."""
    results = await batch_check_annotations(body.urls)
    return {"results": results}


# ── Read ───────────────────────────────────────────────────────────────────────

@app.get("/annotations/{annotation_id}")
async def get_one(annotation_id: str, user: Optional[Identity] = Depends(optional_auth)):
    row = await get_annotation(annotation_id)
    if not row or row["visibility"] != "public":
        raise HTTPException(status_code=404, detail="Annotation not found")
    return to_w3c_annotation(row)


@app.get("/annotations")
async def list_annotations(
    target: Optional[str] = Query(None),
    creator: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: Optional[Identity] = Depends(optional_auth),
):
    rows, total = await query_annotations(
        target_url=target,
        creator=creator,
        since=since,
        visibility_filter=["public"],
        limit=limit,
        offset=offset,
    )
    return to_w3c_page(rows, total, offset)


# ── Write ──────────────────────────────────────────────────────────────────────

@app.post("/annotations", status_code=201)
@limiter.limit("30/minute")
async def create_annotation(
    request: Request,
    body: AnnotationCreate,
    identity: Identity = Depends(verify_token),
):
    if not await get_user(identity.username):
        raise HTTPException(status_code=403, detail="Not registered on this server — call POST /api/register first")
    row = {
        "id": str(uuid.uuid4()),
        "creator": identity.qualified_id,
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
    identity: Identity = Depends(verify_token),
):
    if not await get_user(identity.username):
        raise HTTPException(status_code=403, detail="Not registered on this server — call POST /api/register first")
    row = await get_annotation(annotation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Annotation not found")
    if row["creator"] != identity.qualified_id:
        raise HTTPException(status_code=403, detail="Not your annotation")

    updates = body.model_dump(exclude_none=True)
    updates["modified"] = now_iso()
    await update_annotation(annotation_id, updates)
    saved = await get_annotation(annotation_id)
    return to_w3c_annotation(saved)


@app.delete("/annotations/{annotation_id}", status_code=204)
async def delete_one(
    annotation_id: str,
    identity: Identity = Depends(verify_token),
):
    if not await get_user(identity.username):
        raise HTTPException(status_code=403, detail="Not registered on this server — call POST /api/register first")
    row = await get_annotation(annotation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Annotation not found")
    if row["creator"] != identity.qualified_id:
        raise HTTPException(status_code=403, detail="Not your annotation")
    await delete_annotation(annotation_id)
