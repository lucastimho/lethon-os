from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import numpy as np

from lethon_os.schemas import MemoryShard, UtilityWeights


def _normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    # Zero-vectors map to zero cosine; avoid divide-by-zero without branching.
    return v / np.where(n == 0, 1.0, n)


def cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise cosine similarity. ``a`` may be 1-D or 2-D; ``b`` 1-D or 2-D."""
    a2 = _normalise(np.atleast_2d(a))
    b2 = _normalise(np.atleast_2d(b))
    return a2 @ b2.T


def recency(last_accessed_at: datetime, now: datetime, lambda_decay: float) -> float:
    delta_hours = max((now - last_accessed_at).total_seconds() / 3600.0, 0.0)
    return float(np.exp(-lambda_decay * delta_hours))


def redundancy(
    shard_embedding: np.ndarray,
    reference_embeddings: np.ndarray,
) -> float:
    """Max cosine similarity against a reference window of newer shards.

    The reference set is the pruner's moving window of recent L1 embeddings,
    which commonly *includes the shard itself*. We detect a self-match by
    the top cosine being numerically ≥ 0.9999 and fall back to the second
    best, so a shard does not penalise itself for existing.
    """
    if reference_embeddings.size == 0:
        return 0.0
    sims = cosine(shard_embedding, reference_embeddings)[0]
    top = float(sims.max())
    if top >= 0.9999 and sims.size > 1:
        # Partition is O(n) and avoids fully sorting just to grab second-best.
        return float(np.partition(sims, -2)[-2])
    return top


def compute_utility(
    shard: MemoryShard,
    goal_embedding: np.ndarray,
    reference_embeddings: np.ndarray,
    weights: UtilityWeights,
    now: datetime | None = None,
) -> float:
    """``U(m, t) = αR + βC − γD`` — the single source of truth for scoring."""
    now = now or datetime.now(timezone.utc)
    emb = np.asarray(shard.embedding, dtype=np.float32)

    R = float(cosine(emb, goal_embedding)[0, 0]) if goal_embedding.size else 0.0
    C = recency(shard.last_accessed_at, now, weights.lambda_decay)
    D = redundancy(emb, reference_embeddings)

    return weights.alpha * R + weights.beta * C - weights.gamma * D


def score_batch(
    shards: Iterable[MemoryShard],
    goal_embedding: np.ndarray,
    reference_embeddings: np.ndarray,
    weights: UtilityWeights,
    now: datetime | None = None,
) -> np.ndarray:
    """Vectorised batch scoring — preferred over per-shard in the pruner hot loop."""
    shards = list(shards)
    if not shards:
        return np.empty(0, dtype=np.float32)

    now = now or datetime.now(timezone.utc)
    embs = np.asarray([s.embedding for s in shards], dtype=np.float32)

    if goal_embedding.size:
        R = cosine(embs, goal_embedding)[:, 0]
    else:
        R = np.zeros(len(shards), dtype=np.float32)

    deltas = np.array(
        [(now - s.last_accessed_at).total_seconds() / 3600.0 for s in shards],
        dtype=np.float32,
    )
    C = np.exp(-weights.lambda_decay * np.maximum(deltas, 0.0))

    if reference_embeddings.size:
        sims = cosine(embs, reference_embeddings)
        # Mask the self-match (same id, sim == 1.0) on the diagonal-equivalent.
        # We don't know reference identities here, so we clip the top match if
        # it is numerically 1.0 — that handles the common self-in-reference case.
        top = sims.max(axis=1)
        D = np.where(top >= 0.9999, np.sort(sims, axis=1)[:, -2] if sims.shape[1] > 1 else 0.0, top)
    else:
        D = np.zeros(len(shards), dtype=np.float32)

    return weights.alpha * R + weights.beta * C - weights.gamma * D
