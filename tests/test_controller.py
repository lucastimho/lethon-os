"""MemoryController tests — validate the Cache-Aside walk across tiers."""

from __future__ import annotations

import asyncio

from tests.conftest import make_shard, unit_vec


async def test_put_writes_to_l1_and_l2(controller):
    shard = make_shard(vec=unit_vec(0))
    await controller.put(shard)

    # Both tiers should return the shard.
    assert (await controller.cache.get(shard.id)) is not None
    assert (await controller.vector.get(shard.id)) is not None
    # L3 should not be populated on fresh writes — only the pruner archives.
    assert (await controller.archive.get(shard.id)) is None


async def test_get_hits_l1_fast_path(controller):
    shard = make_shard(vec=unit_vec(0))
    await controller.put(shard)

    result = await controller.get(shard.id)

    assert result is not None
    assert result.id == shard.id


async def test_get_l2_hit_promotes_to_l1(controller):
    shard = make_shard(vec=unit_vec(0))
    await controller.vector.put(shard)  # warm tier only

    assert (await controller.cache.get(shard.id)) is None

    restored = await controller.get(shard.id)

    assert restored is not None
    # Let fire-and-forget touch complete before asserting cache state.
    await asyncio.sleep(0.05)
    assert (await controller.cache.get(shard.id)) is not None, \
        "L2 hit must promote to L1"


async def test_get_l3_hit_restores_to_warm_tiers(controller):
    shard = make_shard(vec=unit_vec(0))
    await controller.archive.put(shard)

    restored = await controller.get(shard.id)

    assert restored is not None
    await asyncio.sleep(0.05)

    assert (await controller.cache.get(shard.id)) is not None
    assert (await controller.vector.get(shard.id)) is not None
    # Archive copy is removed so we don't double-store.
    assert (await controller.archive.get(shard.id)) is None


async def test_get_full_miss_returns_none(controller):
    assert await controller.get("nonexistent-id") is None


async def test_search_promotes_hits_to_l1(controller):
    aligned = make_shard(vec=unit_vec(0))
    other = make_shard(vec=unit_vec(1))
    # Skip L1 on insert to verify promotion on search.
    await controller.vector.put(aligned)
    await controller.vector.put(other)

    hits = await controller.search(query_vector=unit_vec(0), top_k=2)

    assert len(hits) >= 1
    assert hits[0].id == aligned.id
    assert (await controller.cache.get(aligned.id)) is not None


async def test_touch_is_fire_and_forget(controller):
    """Touch must not block the read path — retrieval returns before the
    write-back completes."""
    shard = make_shard(vec=unit_vec(0))
    await controller.put(shard)
    initial_access_count = shard.access_count

    # Back-to-back gets should all succeed without awaiting touch.
    for _ in range(3):
        assert (await controller.get(shard.id)) is not None

    # Eventually touches land.
    await asyncio.sleep(0.05)
    final = await controller.cache.get(shard.id)
    assert final is not None
    assert final.access_count > initial_access_count
