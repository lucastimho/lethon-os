from __future__ import annotations

from redis.asyncio import Redis

from lethon_os.schemas import MemoryShard, Tier

SHARD_KEY = "lethon:shard:{id}"
HOT_ZSET = "lethon:hot"  # score = last_accessed epoch, membership = shard id


class CacheTier:
    """L1 — Redis. Hot shards keyed by id, with a sorted-set eviction index."""

    def __init__(self, client: Redis, capacity: int = 4096, ttl_seconds: int = 3600):
        self._r = client
        self._capacity = capacity
        self._ttl = ttl_seconds

    async def put(self, shard: MemoryShard) -> None:
        shard.tier = Tier.L1
        key = SHARD_KEY.format(id=shard.id)
        blob = shard.model_dump_json()
        score = shard.last_accessed_at.timestamp()

        async with self._r.pipeline(transaction=True) as pipe:
            pipe.set(key, blob, ex=self._ttl)
            pipe.zadd(HOT_ZSET, {shard.id: score})
            await pipe.execute()

        await self._enforce_capacity()

    async def get(self, shard_id: str) -> MemoryShard | None:
        blob = await self._r.get(SHARD_KEY.format(id=shard_id))
        if blob is None:
            return None
        return MemoryShard.model_validate_json(blob)

    async def delete(self, shard_id: str) -> None:
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.delete(SHARD_KEY.format(id=shard_id))
            pipe.zrem(HOT_ZSET, shard_id)
            await pipe.execute()

    async def touch(self, shard: MemoryShard) -> None:
        """Read-path update: refresh last_accessed without going through put."""
        shard.touch()
        await self.put(shard)

    async def scan(self, batch_size: int = 256) -> list[MemoryShard]:
        """Iterate all L1 shards for the pruner."""
        ids = await self._r.zrange(HOT_ZSET, 0, -1)
        out: list[MemoryShard] = []
        for start in range(0, len(ids), batch_size):
            page = ids[start : start + batch_size]
            keys = [
                SHARD_KEY.format(id=sid.decode() if isinstance(sid, bytes) else sid)
                for sid in page
            ]
            blobs = await self._r.mget(keys)
            for blob in blobs:
                if blob is not None:
                    out.append(MemoryShard.model_validate_json(blob))
        return out

    async def newest_embeddings(self, k: int = 64) -> list[list[float]]:
        """Return the embeddings of the k most-recently-accessed shards.

        Used as the redundancy reference window — bounds the pruner's cost
        to O(N · k) rather than O(N²).
        """
        ids = await self._r.zrevrange(HOT_ZSET, 0, k - 1)
        if not ids:
            return []
        keys = [SHARD_KEY.format(id=i.decode() if isinstance(i, bytes) else i) for i in ids]
        blobs = await self._r.mget(keys)
        return [
            MemoryShard.model_validate_json(b).embedding
            for b in blobs
            if b is not None
        ]

    async def _enforce_capacity(self) -> None:
        size = await self._r.zcard(HOT_ZSET)
        if size <= self._capacity:
            return
        overflow = size - self._capacity
        # ZRANGE with scores asc == coldest first
        victims = await self._r.zrange(HOT_ZSET, 0, overflow - 1)
        if not victims:
            return
        async with self._r.pipeline(transaction=True) as pipe:
            for vid in victims:
                v = vid.decode() if isinstance(vid, bytes) else vid
                pipe.delete(SHARD_KEY.format(id=v))
            pipe.zrem(HOT_ZSET, *victims)
            await pipe.execute()

    async def try_acquire_prune_lock(self, ttl_seconds: int = 60) -> bool:
        """At-most-one pruner across horizontally-scaled agents."""
        got = await self._r.set("lethon:prune_lock", "1", nx=True, ex=ttl_seconds)
        return bool(got)
