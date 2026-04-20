"""Contract tests for the Pydantic schemas.

The schemas are the inter-tier wire format. Regressions here cascade into
every store, so these tests guard the invariants the rest of the codebase
assumes.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from lethon_os.schemas import MemoryShard, PruneStats, Tier, UtilityWeights


def test_shard_defaults_are_sane():
    s = MemoryShard(content="x", embedding=[1.0, 0.0])

    assert uuid.UUID(s.id)
    assert s.access_count == 0
    assert 0.999 <= s.utility_score <= 1.001
    assert s.tier is Tier.L1
    assert s.metadata == {}
    assert s.created_at.tzinfo is not None
    assert s.last_accessed_at.tzinfo is not None


def test_shard_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        MemoryShard(content="x", embedding=[1.0], unknown_field="boom")


def test_shard_touch_updates_recency_and_counter():
    s = MemoryShard(content="x", embedding=[1.0])
    before = s.last_accessed_at
    prev = s.access_count

    s.touch()

    assert s.access_count == prev + 1
    assert s.last_accessed_at >= before


def test_shard_json_roundtrip_preserves_all_fields():
    original = MemoryShard(
        content="π",
        embedding=[0.1, -0.2, 0.3],
        goal_context="research flow",
        metadata={"source": "test"},
    )

    blob = original.model_dump_json()
    revived = MemoryShard.model_validate_json(blob)

    assert revived == original


def test_shard_tier_round_trips_through_json():
    s = MemoryShard(content="x", embedding=[1.0], tier=Tier.L3)
    data = json.loads(s.model_dump_json())

    assert data["tier"] == "L3"
    assert MemoryShard.model_validate(data).tier is Tier.L3


def test_utility_weights_defaults_match_blueprint():
    w = UtilityWeights()

    # α + β should dominate γ so signal outweighs penalty in steady state.
    assert w.alpha + w.beta > w.gamma
    assert w.l1_threshold > w.l2_threshold, "L1 must be harder to stay in than L2"
    assert w.lambda_decay > 0


def test_prune_stats_defaults():
    s = PruneStats()

    assert s.scanned == 0
    assert s.demoted_l1_l2 == 0
    assert s.demoted_l2_l3 == 0
    assert s.wall_ms == 0.0
    assert isinstance(s.started_at, datetime)
    assert s.started_at.tzinfo == timezone.utc
