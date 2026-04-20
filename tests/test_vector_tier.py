"""VectorTier (Qdrant / L2) tests — in-process Qdrant at `:memory:`."""

from __future__ import annotations

from lethon_os.schemas import Tier

from tests.conftest import make_shard, unit_vec


async def test_ensure_collection_is_idempotent(vector_tier):
    # First call created the collection via fixture. A second must not raise.
    await vector_tier.ensure_collection()
    await vector_tier.ensure_collection()


async def test_put_and_get_preserves_embedding(vector_tier):
    shard = make_shard(vec=unit_vec(0), content="vector payload")
    await vector_tier.put(shard)

    restored = await vector_tier.get(shard.id)

    assert restored is not None
    assert restored.id == shard.id
    assert restored.content == "vector payload"
    assert restored.embedding == shard.embedding
    assert restored.tier is Tier.L2


async def test_get_miss_returns_none(vector_tier):
    assert await vector_tier.get("no-such-id") is None


async def test_delete_removes_shard(vector_tier):
    shard = make_shard(vec=unit_vec(0))
    await vector_tier.put(shard)
    await vector_tier.delete(shard.id)

    assert await vector_tier.get(shard.id) is None


async def test_search_returns_most_similar_first(vector_tier):
    aligned = make_shard(vec=unit_vec(0), content="aligned")
    orthogonal = make_shard(vec=unit_vec(1), content="orthogonal")
    await vector_tier.put(aligned)
    await vector_tier.put(orthogonal)

    hits = await vector_tier.search(query_vector=unit_vec(0), top_k=2)

    assert len(hits) == 2
    assert hits[0].id == aligned.id


async def test_search_respects_top_k(vector_tier):
    for i in range(4):
        await vector_tier.put(make_shard(vec=unit_vec(i)))

    hits = await vector_tier.search(query_vector=unit_vec(0), top_k=2)
    assert len(hits) == 2


async def test_scan_iterates_entire_collection(vector_tier):
    shards = [make_shard(vec=unit_vec(i)) for i in range(3)]
    for s in shards:
        await vector_tier.put(s)

    scanned = await vector_tier.scan()

    assert {s.id for s in scanned} == {s.id for s in shards}
