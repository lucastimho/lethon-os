from __future__ import annotations

import asyncio
import logging

from lethon_os.schemas import MemoryShard
from lethon_os.tiers import ArchiveTier, CacheTier, VectorTier

log = logging.getLogger("lethon_os.controller")


class MemoryController:
    """Cache-Aside orchestrator across L1 (Redis), L2 (Qdrant), L3 (SQLite).

    The controller is the only component the agent talks to. It is itself
    stateless — shard state lives in the tiers — so multiple agent instances
    can share one logical memory by pointing at the same Redis/Qdrant/SQLite.
    """

    def __init__(self, cache: CacheTier, vector: VectorTier, archive: ArchiveTier):
        self.cache = cache
        self.vector = vector
        self.archive = archive

    # ---- lifecycle ------------------------------------------------------

    async def setup(self) -> None:
        await self.archive.connect()
        await self.vector.ensure_collection()

    async def close(self) -> None:
        await self.archive.close()

    # ---- write path -----------------------------------------------------

    async def put(self, shard: MemoryShard) -> None:
        """New shards enter at L1 and replicate down to L2 for searchability.

        L3 is populated only by the pruner — writing fresh shards to the
        archive would poison it with hot data.
        """
        await self.cache.put(shard)
        await self.vector.put(shard)

    # ---- read path (Cache-Aside) ----------------------------------------

    async def get(self, shard_id: str) -> MemoryShard | None:
        shard = await self.cache.get(shard_id)
        if shard is not None:
            self._fire_touch(shard)
            return shard

        shard = await self.vector.get(shard_id)
        if shard is not None:
            await self.cache.put(shard)
            self._fire_touch(shard)
            return shard

        shard = await self.archive.get(shard_id)
        if shard is not None:
            # Context-restoration: rehydrate through both warm tiers so the
            # next access doesn't re-pay the SQLite round-trip.
            await self.vector.put(shard)
            await self.cache.put(shard)
            await self.archive.delete(shard_id)
            self._fire_touch(shard)
            return shard

        return None

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 8,
        score_threshold: float | None = None,
    ) -> list[MemoryShard]:
        """Vector search spans L1 + L2. L3 is not searched — restoration is
        keyed by id, triggered by an upstream reasoner that already knows
        what it's looking for.
        """
        hits = await self.vector.search(query_vector, top_k, score_threshold)
        for h in hits:
            # Promote every hit back to L1 so subsequent turns are sub-10ms.
            await self.cache.put(h)
        return hits

    # ---- internals ------------------------------------------------------

    def _fire_touch(self, shard: MemoryShard) -> None:
        """Fire-and-forget recency update. Never awaited on the read path —
        retrieval latency is what the user experiences."""
        asyncio.create_task(self._safe_touch(shard))

    async def _safe_touch(self, shard: MemoryShard) -> None:
        try:
            await self.cache.touch(shard)
        except Exception:
            log.exception("touch failed for shard %s", shard.id)
