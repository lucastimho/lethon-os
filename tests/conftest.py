"""Shared fixtures — every test uses hermetic, in-process stores.

No fixture talks to a real Redis, Qdrant, or SQLite file. Tests run fully
offline and in parallel-safe isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import fakeredis.aioredis
import numpy as np
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from lethon_os.controller import MemoryController
from lethon_os.schemas import MemoryShard, Tier
from lethon_os.tiers import ArchiveTier, CacheTier, VectorTier

VECTOR_SIZE = 4  # small, hand-checkable dimension


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield client
    await client.flushall()
    await client.aclose()


@pytest_asyncio.fixture
async def cache_tier(redis_client) -> CacheTier:
    return CacheTier(redis_client, capacity=8, ttl_seconds=60)


@pytest_asyncio.fixture
async def qdrant_client():
    client = AsyncQdrantClient(location=":memory:")
    yield client
    await client.close()


@pytest_asyncio.fixture
async def vector_tier(qdrant_client) -> VectorTier:
    tier = VectorTier(qdrant_client, collection="test_shards", vector_size=VECTOR_SIZE)
    await tier.ensure_collection()
    return tier


@pytest_asyncio.fixture
async def archive_tier() -> ArchiveTier:
    tier = ArchiveTier(db_path=":memory:")
    await tier.connect()
    yield tier
    await tier.close()


@pytest_asyncio.fixture
async def controller(cache_tier, vector_tier, archive_tier) -> MemoryController:
    return MemoryController(cache=cache_tier, vector=vector_tier, archive=archive_tier)


# --- helpers ---------------------------------------------------------------


def make_shard(
    vec: list[float] | None = None,
    *,
    content: str = "hello",
    age_hours: float = 0.0,
    shard_id: str | None = None,
) -> MemoryShard:
    """Build a MemoryShard with controllable recency for deterministic tests."""
    now = datetime.now(timezone.utc)
    ts = now - timedelta(hours=age_hours)
    kwargs = {
        "content": content,
        "embedding": vec if vec is not None else [1.0, 0.0, 0.0, 0.0],
        "created_at": ts,
        "last_accessed_at": ts,
        "tier": Tier.L1,
    }
    if shard_id:
        kwargs["id"] = shard_id
    return MemoryShard(**kwargs)


def unit_vec(i: int, size: int = VECTOR_SIZE) -> list[float]:
    """Return the i-th standard basis vector — handy for orthogonal embeddings."""
    v = np.zeros(size, dtype=np.float32)
    v[i % size] = 1.0
    return v.tolist()
