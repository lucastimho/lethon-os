from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Tier(str, Enum):
    L0_CORE = "L0_CORE"  # Immutable system constitution — pruner has NO access.
    L1 = "L1"            # Redis — hot
    L2 = "L2"            # Qdrant — warm
    L3 = "L3"            # SQLite — cold archive

    @property
    def is_prunable(self) -> bool:
        """``L0_CORE`` holds safety guardrails the pruner must never touch.

        Every code path that demotes, archives, or deletes a shard is
        expected to check this before mutating state. Honor-system at the
        type level; the pruner's runtime guard lands alongside the
        audit-receipt writer in a follow-up change.
        """
        return self is not Tier.L0_CORE


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class MemoryShard(BaseModel):
    """A single unit of agent memory. The schema is the contract between tiers.

    A shard that cannot round-trip through ``MemoryShard.model_validate`` is
    rejected before it touches any store — Qdrant payloads, Redis blobs, and
    SQLite archive rows all deserialise through this class.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    id: str = Field(default_factory=_new_id)
    # Hard caps on user-controlled fields: a malicious payload of unbounded
    # size could OOM the server before it ever reaches the utility loop.
    # 64 KB content and 4096-D embedding cover every real LLM use case
    # (current SOTA is 3072-D); anything past that is suspicious.
    content: str = Field(..., min_length=1, max_length=65_536)
    embedding: list[float] = Field(..., min_length=1, max_length=4096)

    created_at: datetime = Field(default_factory=_utcnow)
    last_accessed_at: datetime = Field(default_factory=_utcnow)
    access_count: int = 0

    utility_score: float = 1.0
    tier: Tier = Tier.L1

    goal_context: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def touch(self) -> None:
        self.last_accessed_at = _utcnow()
        self.access_count += 1


class UtilityWeights(BaseModel):
    """Coefficients for the utility function ``U = αR + βC − γD``.

    ``lambda_decay`` controls recency half-life; a value of 0.08 means a shard
    untouched for ~8.7 hours sees its recency component fall below 0.5.
    """

    model_config = ConfigDict(extra="forbid")

    alpha: float = 0.55          # relevance weight
    beta: float = 0.25           # recency weight
    gamma: float = 0.20          # redundancy penalty
    lambda_decay: float = 0.08   # per-hour exponential decay

    l1_threshold: float = 0.35   # below → demote L1 → L2
    l2_threshold: float = 0.15   # below → demote L2 → L3


class PruneStats(BaseModel):
    """Emitted after each pruner cycle. Consumed by Logfire tracing."""

    model_config = ConfigDict(extra="forbid")

    scanned: int = 0
    demoted_l1_l2: int = 0
    demoted_l2_l3: int = 0
    wall_ms: float = 0.0
    started_at: datetime = Field(default_factory=_utcnow)
