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

    data = resp.json()
    username = data.get("username", "")
    domain = KVSTORE_URL.removeprefix("https://").removeprefix("http://")
    return {"username": username, "did": f"did:web:{domain}:users:{username}"}
