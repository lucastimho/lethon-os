from __future__ import annotations

import gzip
import json
from pathlib import Path

import aiosqlite

from lethon_os.schemas import MemoryShard, Tier


class L0ProtectionError(Exception):
    """Raised when any operation tries to demote, delete, or archive a
    shard living in ``Tier.L0_CORE`` — the agent's safety constitution."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS shards (
    id              TEXT PRIMARY KEY,
    utility_score   REAL NOT NULL,
    last_accessed   TEXT NOT NULL,
    payload_gz      BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shards_last_accessed ON shards(last_accessed);
"""


class ArchiveTier:
    """L3 — SQLite. Cheap cold storage with gzip'd JSON payloads."""

    def __init__(self, db_path: str | Path = "lethon_archive.db"):
        self._path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("ArchiveTier.connect() must be awaited before use")
        return self._db

    async def put(self, shard: MemoryShard) -> None:
        # Defense-in-depth: refuse to archive anything that originated in
        # L0_CORE. The pruner's tier filter should never let an L0 shard
        # reach this point, but this guard catches direct-call bugs and
        # compromised code paths without an I/O round-trip.
        if shard.tier is Tier.L0_CORE:
            raise L0ProtectionError(
                f"cannot archive L0_CORE shard {shard.id} — constitution is immutable",
            )
        shard.tier = Tier.L3
        blob = gzip.compress(shard.model_dump_json().encode("utf-8"))
        await self._conn().execute(
            "INSERT OR REPLACE INTO shards (id, utility_score, last_accessed, payload_gz) "
            "VALUES (?, ?, ?, ?)",
            (shard.id, shard.utility_score, shard.last_accessed_at.isoformat(), blob),
        )
        await self._conn().commit()

    async def get(self, shard_id: str) -> MemoryShard | None:
        cur = await self._conn().execute(
            "SELECT payload_gz FROM shards WHERE id = ?", (shard_id,)
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        raw = gzip.decompress(row[0]).decode("utf-8")
        return MemoryShard.model_validate(json.loads(raw))

    async def delete(self, shard_id: str) -> None:
        await self._conn().execute("DELETE FROM shards WHERE id = ?", (shard_id,))
        await self._conn().commit()

    async def count(self) -> int:
        cur = await self._conn().execute("SELECT COUNT(*) FROM shards")
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row else 0
