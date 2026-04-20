"""UtilityPruner tests — demotion semantics and cross-instance locking."""

from __future__ import annotations

from lethon_os.pruner import UtilityPruner
from lethon_os.schemas import UtilityWeights

from tests.conftest import make_shard, unit_vec


def _permissive_weights() -> UtilityWeights:
    """Weights that let us force demotion deterministically in tests."""
    return UtilityWeights(
        alpha=0.55, beta=0.25, gamma=0.20, lambda_decay=0.5,
        l1_threshold=0.35, l2_threshold=0.15,
    )


async def test_run_once_demotes_stale_l1_shard(controller):
    """A low-utility L1 shard must leave L1 after one cycle.

    Within a single cycle the pruner may cascade L1→L2→L3, so we assert
    the invariant the agent cares about: the shard is no longer hot.
    """
    stale = make_shard(vec=unit_vec(1), age_hours=1000.0)
    await controller.put(stale)

    pruner = UtilityPruner(controller, weights=_permissive_weights())
    pruner.set_goal(unit_vec(0))

    stats = await pruner.run_once()

    assert stats.demoted_l1_l2 >= 1
    assert (await controller.cache.get(stale.id)) is None


async def test_run_once_cascades_through_all_tiers(controller):
    """When utility is far below both thresholds, one cycle should land
    the shard in L3 — L1→L2 then L2→L3 within the same cycle."""
    doomed = make_shard(vec=unit_vec(1), age_hours=1000.0)
    await controller.put(doomed)

    pruner = UtilityPruner(controller, weights=_permissive_weights())
    pruner.set_goal(unit_vec(0))

    await pruner.run_once()

    assert (await controller.cache.get(doomed.id)) is None
    assert (await controller.vector.get(doomed.id)) is None
    assert (await controller.archive.get(doomed.id)) is not None


async def test_run_once_archives_stale_l2_to_l3(controller):
    stale = make_shard(vec=unit_vec(1), age_hours=1000.0)
    await controller.vector.put(stale)  # L2 only
    # Keep L1 empty so this shard is *only* in L2.

    pruner = UtilityPruner(controller, weights=_permissive_weights())
    pruner.set_goal(unit_vec(0))

    stats = await pruner.run_once()

    assert stats.demoted_l2_l3 >= 1
    assert (await controller.vector.get(stale.id)) is None
    assert (await controller.archive.get(stale.id)) is not None


async def test_run_once_retains_high_utility_shards(controller):
    fresh_aligned = make_shard(vec=unit_vec(0), age_hours=0.0)
    await controller.put(fresh_aligned)

    pruner = UtilityPruner(controller, weights=_permissive_weights())
    pruner.set_goal(unit_vec(0))

    stats = await pruner.run_once()

    assert stats.demoted_l1_l2 == 0
    assert (await controller.cache.get(fresh_aligned.id)) is not None


async def test_at_most_one_pruner_across_instances(controller):
    """Two pruner instances sharing the same Redis must not both run."""
    a = UtilityPruner(controller, weights=_permissive_weights())
    b = UtilityPruner(controller, weights=_permissive_weights())
    a.set_goal(unit_vec(0))
    b.set_goal(unit_vec(0))

    # First acquires the lock and runs.
    stats_a = await a.run_once()
    # Second sees the lock held and returns empty stats.
    stats_b = await b.run_once()

    assert stats_b.scanned == 0
    assert stats_b.demoted_l1_l2 == 0
    assert stats_b.demoted_l2_l3 == 0
    # A is allowed to have run (empty tiers are fine — we're only asserting
    # that B was suppressed, not that A did anything in particular).
    assert stats_a.wall_ms >= 0


async def test_goal_update_takes_effect_on_next_cycle(controller):
    # A shard aligned to goal A, but unaligned to goal B.
    shard = make_shard(vec=unit_vec(0), age_hours=0.0)
    await controller.put(shard)

    pruner = UtilityPruner(controller, weights=_permissive_weights())

    # Cycle 1 — goal matches the shard → high utility, retained.
    pruner.set_goal(unit_vec(0))
    s1 = await pruner.run_once()
    assert s1.demoted_l1_l2 == 0

    # Release the lock so the next run executes.
    await controller.cache._r.delete("lethon:prune_lock")

    # Age the shard artificially so recency also drops.
    cached = await controller.cache.get(shard.id)
    assert cached is not None
    from datetime import timedelta
    cached.last_accessed_at = cached.last_accessed_at - timedelta(hours=1000)
    await controller.cache.put(cached)

    # Cycle 2 — goal now orthogonal AND shard is stale → must demote.
    pruner.set_goal(unit_vec(1))
    s2 = await pruner.run_once()
    assert s2.demoted_l1_l2 >= 1


async def test_archive_before_delete_ordering(controller):
    """On L2→L3 demotion, the shard must exist in L3 before leaving L2.

    A reader racing the demotion could otherwise see the shard in zero tiers.
    We verify by checking end-state: post-demotion, it's in L3 and gone from L2.
    """
    shard = make_shard(vec=unit_vec(1), age_hours=1000.0)
    await controller.vector.put(shard)

    pruner = UtilityPruner(controller, weights=_permissive_weights())
    pruner.set_goal(unit_vec(0))
    await pruner.run_once()

    assert (await controller.archive.get(shard.id)) is not None
    assert (await controller.vector.get(shard.id)) is None


async def test_pruner_start_stop_is_clean(controller):
    pruner = UtilityPruner(
        controller,
        weights=_permissive_weights(),
        interval_seconds=0.05,
    )
    pruner.start()
    # Double-start must be a no-op, not raise.
    pruner.start()

    await pruner.stop()
    # Stop is idempotent.
    await pruner.stop()
