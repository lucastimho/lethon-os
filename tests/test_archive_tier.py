"""ArchiveTier (SQLite / L3) tests — in-memory aiosqlite connection."""

from __future__ import annotations

import pytest

from lethon_os.schemas import Tier
from lethon_os.tiers import ArchiveTier

from tests.conftest import make_shard, unit_vec


async def test_connect_creates_schema(archive_tier):
    # `count` reads from the `shards` table — would raise if schema missing.
    assert await archive_tier.count() == 0


async def test_put_and_get_roundtrip(archive_tier):
    shard = make_shard(vec=unit_vec(0), content="archived payload")
    await archive_tier.put(shard)

    restored = await archive_tier.get(shard.id)
    assert restored is not None
    assert restored.content == "archived payload"
    assert restored.embedding == shard.embedding
    assert restored.tier is Tier.L3


async def test_get_miss_returns_none(archive_tier):
    assert await archive_tier.get("nope") is None


async def test_put_is_idempotent_via_upsert(archive_tier):
    shard = make_shard(vec=unit_vec(0), content="v1")
    await archive_tier.put(shard)

    shard.content = "v2"
    await archive_tier.put(shard)  # INSERT OR REPLACE

    restored = await archive_tier.get(shard.id)
    assert restored is not None
    assert restored.content == "v2"
    assert await archive_tier.count() == 1


async def test_delete_removes_row(archive_tier):
    shard = make_shard(vec=unit_vec(0))
    await archive_tier.put(shard)
    await archive_tier.delete(shard.id)

    assert await archive_tier.get(shard.id) is None
    assert await archive_tier.count() == 0


async def test_count_reflects_size(archive_tier):
    for i in range(5):
        await archive_tier.put(make_shard(vec=unit_vec(i)))
    assert await archive_tier.count() == 5


async def test_methods_require_connect():
    # Using the tier before `connect()` must raise, not silently no-op.
    tier = ArchiveTier(db_path=":memory:")
    with pytest.raises(RuntimeError):
        await tier.get("x")
