import os
import httpx
from fastapi import HTTPException, Header
from typing import Optional

from admin_settings import get_setting
from db import DATABASE_URL

KVSTORE_URL_DEFAULT = os.environ.get("KVSTORE_URL", "https://kvstore.mooc.ca")


async def verify_token(authorization: Optional[str] = Header(None)) -> dict:
    """Verify Bearer token against KVStore. Returns {"did": ..., "username": ...}."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ")
    kvstore_url = await get_setting(DATABASE_URL, "KVSTORE_URL", default=KVSTORE_URL_DEFAULT)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{kvstore_url}/auth/verify",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"KVStore unreachable: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    data = resp.json()
    username = data.get("username", "")
    domain = kvstore_url.removeprefix("https://").removeprefix("http://")
    return {"username": username, "did": f"did:web:{domain}:users:{username}"}
