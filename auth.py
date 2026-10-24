import os
from typing import Optional

from fastapi import Depends, Header, HTTPException

from clist_kvstore_auth.asyncio import Identity, verify_token as _kv_verify_token
from clist_server_admin.fastapi import get_setting
from db import DATABASE_URL

KVSTORE_URL_DEFAULT = os.environ.get("KVSTORE_URL", "https://kvstore.mooc.ca")
ADMIN_USERNAMES = {u.strip() for u in os.environ.get("ADMIN_USERNAMES", "").split(",") if u.strip()}


async def _kvstore_url() -> str:
    return await get_setting(DATABASE_URL, "KVSTORE_URL", default=KVSTORE_URL_DEFAULT)


async def verify_token(authorization: Optional[str] = Header(None)) -> Identity:
    """FastAPI dependency: verify a Bearer token.

    Local JWT/JWKS verification ("Pattern B") against whichever issuer the
    token's own `iss` claim names — open to any kvstore instance, no
    allowlist. Pre-migration opaque tokens carry no `iss` and fall back to
    GET {KVSTORE_URL}/auth/verify.
    """
    identity, err = await _kv_verify_token(authorization, await _kvstore_url())
    if err:
        body, status = err
        raise HTTPException(status_code=status, detail=body.get("error", "Unauthorized"))
    return identity


async def is_admin(identity: Identity) -> bool:
    # Reimplemented locally (not clist_kvstore_auth.asyncio.is_admin, and not
    # clist_server_admin.fastapi.is_admin) because the trusted admin issuer
    # is this app's own KVSTORE_URL setting, which is DB-overridable via
    # /admin/settings, not just a static env var — and because neither
    # shared package's own is_admin() checks the issuer, only the username.
    return bool(identity) and identity.username in ADMIN_USERNAMES and identity.iss == await _kvstore_url()


async def admin_dependency(identity: Identity = Depends(verify_token)) -> Identity:
    """FastAPI dependency: verify_token, then require admin + trusted issuer."""
    if not await is_admin(identity):
        raise HTTPException(status_code=403, detail="Forbidden")
    return identity
