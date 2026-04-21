"""HTTP request/response models.

Kept separate from the core `MemoryShard` contract so the wire format can
evolve (e.g. redacting embeddings from responses, adding HATEOAS links)
without coupling to internal state. Every model is ``extra="forbid"`` so
typos in client payloads surface as 422s rather than silent no-ops.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lethon_os.schemas import Tier


class PutShardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1)
    embedding: list[float] = Field(..., min_length=1)
    goal_context: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_vector: list[float] = Field(..., min_length=1)
    top_k: int = Field(default=8, ge=1, le=128)
    score_threshold: float | None = Field(default=None, ge=-1.0, le=1.0)


class GoalUpdateRequest(BaseModel):
    """Rotate the pruner's goal vector. Takes effect on the next cycle."""

    model_config = ConfigDict(extra="forbid")

    goal_embedding: list[float] = Field(..., min_length=1)


class ShardResponse(BaseModel):
    """Shard view returned to clients. Embedding is omitted by default —
    raw vectors are an implementation detail, not a public contract."""

    model_config = ConfigDict(extra="forbid")

    id: str
    content: str
    tier: Tier
    utility_score: float
    access_count: int
    last_accessed_at: datetime
    goal_context: str | None = None


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hits: list[ShardResponse]
    latency_ms: float


class HealthResponse(BaseModel):
    """Liveness + operational signal. ``back_pressure=True`` means the
    gateway is shedding writes until the pruner catches up."""

    model_config = ConfigDict(extra="forbid")

    status: str  # "ok" | "degraded"
    l1_size: int
    l2_size: int
    l3_size: int
    prune_lag_seconds: float | None = None
    writes_since_prune: int = 0
    back_pressure: bool = False


class PruneStatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scanned: int
    demoted_l1_l2: int
    demoted_l2_l3: int
    wall_ms: float
    started_at: datetime
