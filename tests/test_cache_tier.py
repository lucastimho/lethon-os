"""CacheTier (Redis / L1) tests — fakeredis provides the backend."""

from __future__ import annotations

import asyncio

from tests.conftest import make_shard, unit_vec


async def test_put_and_get_roundtrip(cache_tier):
    shard = make_shard(vec=unit_vec(0))
    await cache_tier.put(shard)

    restored = await cache_tier.get(shard.id)
    assert restored is not None
    assert restored.id == shard.id
    assert restored.content == shard.content
    assert restored.embedding == shard.embedding


async def test_get_miss_returns_none(cache_tier):
    assert await cache_tier.get("nonexistent") is None


async def test_delete_removes_shard_and_zset_entry(cache_tier, redis_client):
    shard = make_shard(vec=unit_vec(0))
    await cache_tier.put(shard)

    await cache_tier.delete(shard.id)

    assert await cache_tier.get(shard.id) is None
    assert await redis_client.zscore("lethon:hot", shard.id) is None


async def test_touch_updates_last_accessed(cache_tier):
    shard = make_shard(vec=unit_vec(0), age_hours=5.0)
    await cache_tier.put(shard)

    await asyncio.sleep(0.01)
    await cache_tier.touch(shard)

    refreshed = await cache_tier.get(shard.id)
    assert refreshed is not None
    assert refreshed.access_count == 1
    assert refreshed.last_accessed_at > refreshed.created_at


async def test_capacity_eviction_drops_coldest_first(redis_client):
    # Tiny capacity to force immediate eviction.
    from lethon_os.tiers import CacheTier

    tier = CacheTier(redis_client, capacity=3, ttl_seconds=60)

    shards = [make_shard(vec=unit_vec(i), age_hours=float(10 - i)) for i in range(5)]
    # Insert in order — older shards get older last_accessed_at, so they
    # hold the LOWEST zset scores and should be evicted first.
    for s in shards:
        await tier.put(s)

    remaining_ids = {s.id for s in shards if await tier.get(s.id) is not None}
    # Capacity 3 but we inserted 5; two oldest must be gone.
    assert len(remaining_ids) == 3
    # The 2 evicted should be the oldest (highest age_hours → shards[0:2]).
    assert shards[0].id not in remaining_ids
    assert shards[1].id not in remaining_ids


async def test_scan_returns_all_shards(cache_tier):
    shards = [make_shard(vec=unit_vec(i)) for i in range(5)]
    for s in shards:
        await cache_tier.put(s)

    scanned = list(await cache_tier.scan())
    assert {s.id for s in scanned} == {s.id for s in shards}


async def test_newest_embeddings_returns_most_recent(cache_tier):
    # Older shard inserted first.
    old = make_shard(vec=unit_vec(0), age_hours=100.0)
    new = make_shard(vec=unit_vec(1), age_hours=0.0)
    await cache_tier.put(old)
    await cache_tier.put(new)

    newest = await cache_tier.newest_embeddings(k=1)

    assert len(newest) == 1
    assert newest[0] == new.embedding


async def test_prune_lock_is_at_most_one(cache_tier):
    first = await cache_tier.try_acquire_prune_lock(ttl_seconds=5)
    second = await cache_tier.try_acquire_prune_lock(ttl_seconds=5)

    assert first is True
    assert second is False


async def test_prune_lock_reacquirable_after_ttl(cache_tier, redis_client):
    assert await cache_tier.try_acquire_prune_lock(ttl_seconds=60) is True
    # Manually expire the lock as if TTL had elapsed.
    await redis_client.delete("lethon:prune_lock")
    assert await cache_tier.try_acquire_prune_lock(ttl_seconds=60) is True
