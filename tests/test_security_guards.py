"""L0_CORE guards + Pydantic bounds + pruner filter."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lethon_os.pruner import UtilityPruner
from lethon_os.schemas import MemoryShard, Tier, UtilityWeights
from lethon_os.tiers.archive import L0ProtectionError

from tests.conftest import make_shard, unit_vec


# ---------------------------------------------------------------------------
# Pydantic bounds (app-layer buffer defense)
# ---------------------------------------------------------------------------


def test_content_too_long_rejected():
    with pytest.raises(ValidationError):
        MemoryShard(content="x" * 65_537, embedding=[1.0])


def test_empty_content_rejected():
    with pytest.raises(ValidationError):
        MemoryShard(content="", embedding=[1.0])


def test_empty_embedding_rejected():
    with pytest.raises(ValidationError):
        MemoryShard(content="x", embedding=[])


def test_embedding_too_wide_rejected():
    with pytest.raises(ValidationError):
        MemoryShard(content="x", embedding=[0.0] * 4097)


# ---------------------------------------------------------------------------
# Tier partitioning
# ---------------------------------------------------------------------------


def test_l0_core_tier_is_not_prunable():
    assert Tier.L0_CORE.is_prunable is False
    assert Tier.L1.is_prunable is True
    assert Tier.L2.is_prunable is True
    assert Tier.L3.is_prunable is True


async def test_archive_put_rejects_l0_shard(archive_tier):
    shard = make_shard(vec=unit_vec(0))
    shard.tier = Tier.L0_CORE

    with pytest.raises(L0ProtectionError, match="immutable"):
        await archive_tier.put(shard)


# ---------------------------------------------------------------------------
# Pruner filter — L0 shards never enter the demote loop
# ---------------------------------------------------------------------------


async def test_pruner_skips_l0_shards_in_l1(controller):
    """An L0_CORE shard that somehow lives in the L1 cache must be
    filtered out of the scan before scoring. The guard makes
    decision-by-decay impossible on the constitution."""
    constitution = make_shard(vec=unit_vec(0), age_hours=1000.0, content="Do no harm.")
    constitution.tier = Tier.L0_CORE
    await controller.cache.put(constitution)
    # Restore the tier after cache.put overwrote it to L1.
    constitution.tier = Tier.L0_CORE
    await controller.cache.put(constitution)

    pruner = UtilityPruner(
        controller,
        weights=UtilityWeights(
            alpha=0.55, beta=0.25, gamma=0.20, lambda_decay=0.5,
            l1_threshold=0.35, l2_threshold=0.15,
        ),
    )
    pruner.set_goal(unit_vec(1))  # orthogonal — would normally trigger demotion

    stats = await pruner.run_once()

    # No demotion happened because the L0 shard was skipped.
    assert stats.demoted_l1_l2 == 0
    assert (await controller.cache.get(constitution.id)) is not None


# ---------------------------------------------------------------------------
# Pruner audit hook
# ---------------------------------------------------------------------------


async def test_pruner_on_action_fires_for_demotion(controller):
    captured: list[tuple[str, str, str, str]] = []

    async def hook(shard, action, from_tier, to_tier):
        captured.append((shard.id, action, from_tier.value, to_tier.value))

    stale = make_shard(vec=unit_vec(1), age_hours=1000.0)
    await controller.put(stale)

    pruner = UtilityPruner(
        controller,
        weights=UtilityWeights(
            alpha=0.55, beta=0.25, gamma=0.20, lambda_decay=0.5,
            l1_threshold=0.35, l2_threshold=0.15,
        ),
        on_action=hook,
    )
    pruner.set_goal(unit_vec(0))

    await pruner.run_once()

    # Single shard that cascades L1→L2 and L2→L3 in one cycle emits two events.
    actions = [a for _, a, _, _ in captured]
    assert "demote" in actions
    assert "archive" in actions


async def test_pruner_hook_failure_does_not_poison_loop(controller):
    """A broken audit sink must log + continue, never deadlock the pruner."""
    calls: list[str] = []

    async def failing_hook(shard, action, from_tier, to_tier):
        calls.append(action)
        raise RuntimeError("sink offline")

    stale = make_shard(vec=unit_vec(1), age_hours=1000.0)
    await controller.put(stale)

    pruner = UtilityPruner(
        controller,
        weights=UtilityWeights(
            alpha=0.55, beta=0.25, gamma=0.20, lambda_decay=0.5,
            l1_threshold=0.35, l2_threshold=0.15,
        ),
        on_action=failing_hook,
    )
    pruner.set_goal(unit_vec(0))

    # No exception escapes — pruner absorbs the hook failure.
    stats = await pruner.run_once()

    assert stats.demoted_l1_l2 >= 1
    assert len(calls) >= 1  # hook was invoked before it raised
