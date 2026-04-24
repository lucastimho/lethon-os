"""Append-only audit log — persists signed receipts with full chain replay.

Every append verifies two things synchronously before the receipt hits
disk:

  1. **Signature** — the receipt's bytes match its signature under the
     public key resolved from :class:`KeyRegistry`.
  2. **Chain link** — the receipt's ``prev_receipt_hash`` equals the
     hash of the current log tip. Insertions, deletions, and reorderings
     are all caught by this single check.

Replay (:meth:`verify_chain`) walks the log from genesis to tip and
re-runs both checks for every receipt. It is O(N) on the log size and
intended for startup / periodic audits, not for the write path.

Merkle root (:meth:`merkle_root`) gives an O(1) commitment that can be
published to an external notary so third parties can later prove any
receipt was in the log at that time.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import aiosqlite

from lethon_os.security.keys import KeyRegistry
from lethon_os.security.schemas import SignedAuditReceipt
from lethon_os.security.signing import IntegrityError, verify_receipt


class ChainError(IntegrityError):
    """Raised when the audit log's chain link is broken or out of order."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    sequence          INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_hash      TEXT NOT NULL UNIQUE,
    prev_receipt_hash TEXT,
    shard_id          TEXT NOT NULL,
    action            TEXT NOT NULL,
    actor_key_id      TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    payload_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_shard ON audit_log(shard_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts    ON audit_log(timestamp);
"""


class SignedAuditLog:
    """Append-only SQLite-backed audit log with signature + chain enforcement."""

    def __init__(self, db_path: str | Path, keys: KeyRegistry) -> None:
        self._path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._keys = keys
        # Coarse write lock: receipts must be ordered and the chain tip
        # read-modify-write must be atomic. A process-local lock is enough
        # for a single pruner (which is already at-most-one via Redis).
        self._lock = asyncio.Lock()

    # ---- lifecycle ------------------------------------------------------

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
            raise RuntimeError("SignedAuditLog.connect() must be awaited before use")
        return self._db

    # ---- writes ---------------------------------------------------------

    async def append(self, receipt: SignedAuditReceipt) -> None:
        """Verify + persist. Fails atomically on any integrity issue — no
        partial state ever reaches the log."""
        verifier = self._keys.get(receipt.actor_key_id)
        verify_receipt(receipt, verifier)  # raises IntegrityError on mismatch

        async with self._lock:
            tip = await self._chain_tip_locked()
            if tip != receipt.prev_receipt_hash:
                raise ChainError(
                    f"chain tip mismatch: log tip={tip!r}, "
                    f"receipt prev={receipt.prev_receipt_hash!r}",
                )

            await self._conn().execute(
                "INSERT INTO audit_log "
                "(receipt_hash, prev_receipt_hash, shard_id, action, "
                " actor_key_id, timestamp, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt.receipt_hash,
                    receipt.prev_receipt_hash,
                    receipt.shard_id,
                    receipt.action.value,
                    receipt.actor_key_id,
                    receipt.timestamp.isoformat(),
                    receipt.model_dump_json(),
                ),
            )
            await self._conn().commit()

    # ---- reads ----------------------------------------------------------

    async def tip(self) -> str | None:
        """Current chain-tip hash, for chaining the next receipt."""
        async with self._lock:
            return await self._chain_tip_locked()

    async def _chain_tip_locked(self) -> str | None:
        cur = await self._conn().execute(
            "SELECT receipt_hash FROM audit_log ORDER BY sequence DESC LIMIT 1",
        )
        row = await cur.fetchone()
        await cur.close()
        return row[0] if row else None

    async def count(self) -> int:
        cur = await self._conn().execute("SELECT COUNT(*) FROM audit_log")
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row else 0

    async def fetch_all(self) -> list[SignedAuditReceipt]:
        cur = await self._conn().execute(
            "SELECT payload_json FROM audit_log ORDER BY sequence ASC",
        )
        rows = await cur.fetchall()
        await cur.close()
        return [SignedAuditReceipt.model_validate_json(r[0]) for r in rows]

    async def for_shard(self, shard_id: str) -> list[SignedAuditReceipt]:
        cur = await self._conn().execute(
            "SELECT payload_json FROM audit_log WHERE shard_id = ? "
            "ORDER BY sequence ASC",
            (shard_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [SignedAuditReceipt.model_validate_json(r[0]) for r in rows]

    # ---- integrity ------------------------------------------------------

    async def verify_chain(self) -> None:
        """Full end-to-end replay. Raises :class:`ChainError` on the first
        break; the caller's next step is usually "freeze writes and page a
        human." """
        receipts = await self.fetch_all()
        prev: str | None = None
        for i, r in enumerate(receipts):
            if r.prev_receipt_hash != prev:
                raise ChainError(
                    f"chain broken at seq {i}: "
                    f"expected prev={prev!r}, got {r.prev_receipt_hash!r}",
                )
            # Signature is independent of chain position; catching both
            # classes of tampering in a single pass is the point.
            verify_receipt(r, self._keys.get(r.actor_key_id))
            prev = r.receipt_hash

    async def merkle_root(self) -> str | None:
        """BLAKE2b-256 Merkle root over all receipt hashes in order.

        Odd-count layers duplicate the last node (Bitcoin convention).
        This is vulnerable in principle to CVE-2012-2459-style duplication
        attacks — fine for our threat model since every receipt hash is
        already uniquely signed, but noted here in case the usage context
        ever changes.
        """
        cur = await self._conn().execute(
            "SELECT receipt_hash FROM audit_log ORDER BY sequence ASC",
        )
        hashes = [bytes.fromhex(row[0]) for row in await cur.fetchall()]
        await cur.close()

        if not hashes:
            return None

        layer = hashes
        while len(layer) > 1:
            nxt: list[bytes] = []
            for i in range(0, len(layer), 2):
                left = layer[i]
                right = layer[i + 1] if i + 1 < len(layer) else left
                nxt.append(hashlib.blake2b(left + right, digest_size=32).digest())
            layer = nxt
        return layer[0].hex()
