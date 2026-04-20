"""Utility-function tests.

The scoring formula U = αR + βC − γD is the system's only source of truth
for memory value. These tests pin its mathematical behavior so future
refactors can't silently change which shards survive.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from lethon_os.schemas import MemoryShard, UtilityWeights
from lethon_os.utility import (
    compute_utility,
    cosine,
    recency,
    redundancy,
    score_batch,
)

from tests.conftest import make_shard


def test_cosine_identical_vectors_is_one():
    v = np.array([0.3, 0.4, 0.5])
    assert cosine(v, v)[0, 0] == pytest.approx(1.0, abs=1e-6)


def test_cosine_orthogonal_vectors_is_zero():
    a = np.array([1.0, 0.0, 0.0])
    b = np.array([0.0, 1.0, 0.0])
    assert cosine(a, b)[0, 0] == pytest.approx(0.0, abs=1e-6)


def test_cosine_handles_zero_vector_without_nan():
    zero = np.zeros(3)
    v = np.array([1.0, 2.0, 3.0])
    # Normalising a zero vector used to be a divide-by-zero. Guard must hold.
    out = cosine(zero, v)
    assert not np.isnan(out).any()


def test_recency_at_now_is_one():
    now = datetime.now(timezone.utc)
    assert recency(now, now, lambda_decay=0.08) == pytest.approx(1.0)


def test_recency_decays_monotonically():
    now = datetime.now(timezone.utc)
    earlier = now - timedelta(hours=5)
    older = now - timedelta(hours=50)

    assert recency(earlier, now, 0.08) > recency(older, now, 0.08)


def test_recency_clamps_future_timestamps_at_one():
    # A shard with last_accessed "in the future" (clock skew) must not
    # receive a bonus score — clamp Δt at zero.
    now = datetime.now(timezone.utc)
    future = now + timedelta(hours=3)
    assert recency(future, now, 0.08) == pytest.approx(1.0)


def test_redundancy_empty_reference_set_is_zero():
    v = np.array([1.0, 0.0])
    assert redundancy(v, np.empty((0, 2))) == 0.0


def test_redundancy_self_in_reference_masks_to_second_best():
    # When a shard appears in its own reference window (pruner's default),
    # its self-cosine of ~1.0 should be masked in favor of the next-best.
    shard_vec = np.array([1.0, 0.0])
    others = np.array([[1.0, 0.0], [0.1, 0.99]])  # self + near-orthogonal
    w = UtilityWeights()
    shard = make_shard(vec=shard_vec.tolist())
    scores = score_batch([shard], np.array([1.0, 0.0]), others, w)
    # D should be ~0.1 (second-best), not ~1.0 (self-match)
    # → R=1, C=1, D≈0.1 → U ≈ 0.55 + 0.25 − 0.02 ≈ 0.78
    assert scores[0] > 0.5


def test_compute_utility_rewards_relevance():
    w = UtilityWeights()
    goal = np.array([1.0, 0.0, 0.0, 0.0])

    aligned = make_shard(vec=[1.0, 0.0, 0.0, 0.0])
    orthogonal = make_shard(vec=[0.0, 1.0, 0.0, 0.0])

    u_aligned = compute_utility(aligned, goal, np.empty((0, 4)), w)
    u_ortho = compute_utility(orthogonal, goal, np.empty((0, 4)), w)

    assert u_aligned > u_ortho


def test_compute_utility_penalises_redundancy():
    w = UtilityWeights()
    goal = np.array([1.0, 0.0, 0.0, 0.0])
    shard = make_shard(vec=[1.0, 0.0, 0.0, 0.0])

    no_dup = compute_utility(shard, goal, np.empty((0, 4)), w)
    with_dup = compute_utility(
        shard, goal, np.array([[1.0, 0.0, 0.0, 0.0]]) * 0.95, w,
    )

    assert with_dup < no_dup


def test_score_batch_matches_scalar_compute_utility():
    """Vectorised batch must produce the same numbers as per-shard scoring."""
    w = UtilityWeights()
    goal = np.array([1.0, 0.0, 0.0, 0.0])
    refs = np.array([[0.0, 1.0, 0.0, 0.0], [0.5, 0.5, 0.0, 0.0]])

    shards = [
        make_shard(vec=[1.0, 0.0, 0.0, 0.0]),
        make_shard(vec=[0.0, 1.0, 0.0, 0.0], age_hours=10.0),
        make_shard(vec=[0.0, 0.0, 1.0, 0.0], age_hours=100.0),
    ]

    batch = score_batch(shards, goal, refs, w)
    scalar = np.array([compute_utility(s, goal, refs, w) for s in shards])

    np.testing.assert_allclose(batch, scalar, rtol=1e-4, atol=1e-4)


def test_score_batch_empty_input():
    out = score_batch(
        [], np.array([1.0, 0.0]), np.empty((0, 2)), UtilityWeights(),
    )
    assert out.shape == (0,)
