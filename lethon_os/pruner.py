from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

import numpy as np

from lethon_os.controller import MemoryController
from lethon_os.schemas import MemoryShard, PruneStats, Tier, UtilityWeights
from lethon_os.utility import score_batch

log = logging.getLogger("lethon_os.pruner")

# Optional audit hook. Signature mirrors the state transition:
#   (shard, action_name, from_tier, to_tier) -> None
# `action_name` is a plain string to keep this module free of any
# dependency on `lethon_os.security`.
PrunerHook = Callable[
    [MemoryShard, str, Tier, Tier],
    Awaitable[None],
]


class UtilityPruner:
    """Background demotion worker. Never blocks the agent's reasoning loop.

    Each cycle:
      1. Acquires a Redis-backed at-most-one lock (safe across instances).
      2. Scores every shard in L1 and L2 against the current goal.
      3. Demotes shards under threshold one tier down.
      4. Emits PruneStats for tracing.
    """

    def __init__(
        self,
        controller: MemoryController,
        weights: UtilityWeights | None = None,
        interval_seconds: float = 30.0,
        reference_window: int = 64,
        on_action: PrunerHook | None = None,
    ):
        self.controller = controller
        self.weights = weights or UtilityWeights()
        self.interval = interval_seconds
        self.reference_window = reference_window
        self._on_action = on_action

        self._goal_embedding: np.ndarray = np.zeros(0, dtype=np.float32)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    # ---- goal management ------------------------------------------------

    def set_goal(self, goal_embedding: list[float] | np.ndarray | None) -> None:
        """Update the global goal vector. Read on the next cycle — no need
        to lock; float-array assignment is atomic enough for our purposes."""
        if goal_embedding is None:
            self._goal_embedding = np.zeros(0, dtype=np.float32)
        else:
            self._goal_embedding = np.asarray(goal_embedding, dtype=np.float32)

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="lethon-pruner")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # ---- main loop ------------------------------------------------------

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                log.exception("pruner cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> PruneStats:
        """One pruning cycle. Public for tests and on-demand triggers."""
        t0 = time.perf_counter()
        stats = PruneStats()

        if not await self.controller.cache.try_acquire_prune_lock(
            ttl_seconds=int(self.interval * 2)
        ):
            log.debug("another instance holds the prune lock — skipping cycle")
            return stats

        ref_embs = await self.controller.cache.newest_embeddings(self.reference_window)
        ref_array = np.asarray(ref_embs, dtype=np.float32) if ref_embs else np.empty((0, 0), dtype=np.float32)
        now = datetime.now(timezone.utc)

        # Security boundary: filter non-prunable tiers (L0_CORE) out of the
        # demote loop. The agent's safety constitution lives in L0 and must
        # never be touched by utility decay.
        l1_shards = [
            s for s in await self.controller.cache.scan() if s.tier.is_prunable
        ]
        l2_shards = [
            s for s in await self.controller.vector.scan() if s.tier.is_prunable
        ]

        stats.demoted_l1_l2 = await self._demote_tier(
            shards=l1_shards,
            ref_array=ref_array,
            now=now,
            threshold=self.weights.l1_threshold,
            demote=self._demote_l1_to_l2,
        )

        stats.demoted_l2_l3 = await self._demote_tier(
            shards=l2_shards,
            ref_array=ref_array,
            now=now,
            threshold=self.weights.l2_threshold,
            demote=self._demote_l2_to_l3,
        )

        stats.scanned = stats.demoted_l1_l2 + stats.demoted_l2_l3
        stats.wall_ms = (time.perf_counter() - t0) * 1000.0
        log.info(
            "prune cycle: demoted L1→L2=%d L2→L3=%d wall=%.1fms",
            stats.demoted_l1_l2, stats.demoted_l2_l3, stats.wall_ms,
        )
        return stats

    # ---- demotion primitives -------------------------------------------

    async def _demote_tier(
        self,
        shards: list[MemoryShard],
        ref_array: np.ndarray,
        now: datetime,
        threshold: float,
        demote,
    ) -> int:
        if not shards:
            return 0

        scores = score_batch(
            shards=shards,
            goal_embedding=self._goal_embedding,
            reference_embeddings=ref_array,
            weights=self.weights,
            now=now,
        )

        demoted = 0
        for shard, score in zip(shards, scores):
            shard.utility_score = float(score)
            if score < threshold:
                await demote(shard)
                demoted += 1
        return demoted

    async def _demote_l1_to_l2(self, shard: MemoryShard) -> None:
        # L2 already has this shard (write-through on put), so we just drop
        # it from the cache. Score is persisted on next L2 touch.
        await self.controller.cache.delete(shard.id)
        await self._emit("demote", shard, Tier.L1, Tier.L2)

    async def _demote_l2_to_l3(self, shard: MemoryShard) -> None:
        # Ordered to avoid a window where the shard exists nowhere:
        #   archive first, then drop from L2.
        await self.controller.archive.put(shard)
        await self.controller.vector.delete(shard.id)
        # Defensive: if it lingered in L1, remove it.
        await self.controller.cache.delete(shard.id)
        await self._emit("archive", shard, Tier.L2, Tier.L3)

    async def _emit(
        self, action: str, shard: MemoryShard, from_tier: Tier, to_tier: Tier,
    ) -> None:
        """Invoke the optional audit hook. Failures are logged, never swallowed
        into the demote loop — a broken audit sink should not poison the
        pruner, but it must be visible in the logs."""
        if self._on_action is None:
            return
        try:
            await self._on_action(shard, action, from_tier, to_tier)
        except Exception:
            log.exception(
                "audit hook failed for %s: %s %s→%s",
                shard.id, action, from_tier.value, to_tier.value,
            )
