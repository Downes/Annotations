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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username      TEXT PRIMARY KEY,
                did           TEXT NOT NULL,
                registered_at TEXT NOT NULL
            )
        """)
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
    placeholders = ",".join("?" * len(visibility_filter))
    conditions = [f"visibility IN ({placeholders})"]
    params: list = list(visibility_filter)

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
                row["id"],
                row["creator"],
                row["target_url"],
                row.get("target_selector"),
                row["body"],
                json.dumps(row.get("tags", [])),
                row["created"],
                None,
                row["visibility"],
                row["motivation"],
                None,
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


async def register_user(username: str, did: str) -> dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (username, did, registered_at) VALUES (?, ?, ?)",
            (username, did, now)
        )
        await db.commit()
    return {"username": username, "did": did, "registered_at": now}


async def get_user(username: str) -> dict | None:
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE username = ?", (username,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def batch_check_annotations(urls: list[str]) -> dict[str, int]:
    """Return counts of public/followers annotations for each URL that has at least one."""
    if not urls:
        return {}
    placeholders = ",".join("?" * len(urls))
    async with aiosqlite.connect(DATABASE_URL) as db:
        async with db.execute(
            f"""SELECT target_url, COUNT(*) as cnt
                FROM annotations
                WHERE target_url IN ({placeholders})
                  AND visibility = 'public'
                GROUP BY target_url""",
            urls,
        ) as cursor:
            return {row[0]: row[1] for row in await cursor.fetchall()}
