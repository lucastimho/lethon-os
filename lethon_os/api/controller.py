"""UtilityController — operational facade over MemoryController.

Responsibilities that don't belong in the pure storage layer:
  * Running the pruning schedule (default 60s) and tracking its lag
  * Back-pressure: rejecting writes when the pruner is too far behind
  * Logfire span instrumentation for every read/write/prune op
  * HTTP-friendly projections of internal shard state

The underlying three-tier storage logic stays in :class:`MemoryController`;
this class only adds the operational skin that a FastAPI app exposes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import contextmanager
from typing import Iterator

from lethon_os.api.schemas import (
    HealthResponse,
    PruneStatsResponse,
    PutShardRequest,
    SearchRequest,
    SearchResponse,
    ShardResponse,
)
from lethon_os.controller import MemoryController
from lethon_os.pruner import UtilityPruner
from lethon_os.schemas import MemoryShard

log = logging.getLogger("lethon_os.api")

try:  # Logfire is optional — the core library stays importable without it.
    import logfire as _logfire  # type: ignore
except ImportError:  # pragma: no cover
    _logfire = None


class BackPressure(Exception):
    """Raised when the pruner is behind by more than the configured budget.

    FastAPI should translate this into an HTTP 503 with a Retry-After hint.
    The client is expected to back off; forcing a write through would let
    L1 grow unbounded and re-introduce the context-rot we built the pruner
    to prevent.
    """


class UtilityController:
    """High-level facade exposed by the FastAPI app.

    This class drives the pruner itself rather than calling
    ``pruner.start()``. That inversion keeps all telemetry and back-pressure
    bookkeeping co-located: every cycle flows through ``_run_cycle`` where
    we can timestamp completion, reset counters, and emit Logfire metrics.
    """

    def __init__(
        self,
        memory: MemoryController,
        pruner: UtilityPruner,
        *,
        prune_interval_seconds: float = 60.0,
        max_prune_lag_seconds: float = 300.0,
        max_writes_between_prunes: int = 2_000,
    ):
        self._mem = memory
        self._pruner = pruner
        self._prune_interval = prune_interval_seconds
        self._max_prune_lag = max_prune_lag_seconds
        self._max_writes_gap = max_writes_between_prunes

        self._last_prune_ts: float | None = None
        self._writes_since_prune = 0
        self._last_stats: PruneStatsResponse | None = None

        self._schedule_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    # ---- lifecycle ------------------------------------------------------

    async def setup(self) -> None:
        await self._mem.setup()
        self._stop.clear()
        self._schedule_task = asyncio.create_task(
            self._schedule_loop(), name="utility-schedule"
        )

    async def close(self) -> None:
        self._stop.set()
        if self._schedule_task is not None:
            self._schedule_task.cancel()
            try:
                await self._schedule_task
            except (asyncio.CancelledError, Exception):
                pass
            self._schedule_task = None
        await self._mem.close()

    # ---- write path -----------------------------------------------------

    async def put(self, req: PutShardRequest) -> ShardResponse:
        if self._back_pressure_active():
            raise BackPressure(
                f"pruner lag {self._prune_lag_seconds():.0f}s, "
                f"writes-since-prune {self._writes_since_prune}"
            )

        shard = MemoryShard(
            content=req.content,
            embedding=req.embedding,
            goal_context=req.goal_context,
            metadata=req.metadata,
        )

        with self._span("put_shard", shard_id=shard.id, vec_dim=len(req.embedding)):
            await self._mem.put(shard)

        self._writes_since_prune += 1
        return self._to_response(shard)

    # ---- read path ------------------------------------------------------

    async def get(self, shard_id: str) -> ShardResponse | None:
        with self._span("get_shard", shard_id=shard_id):
            shard = await self._mem.get(shard_id)
        return self._to_response(shard) if shard else None

    async def search(self, req: SearchRequest) -> SearchResponse:
        t0 = time.perf_counter()
        with self._span("search", top_k=req.top_k, threshold=req.score_threshold):
            hits = await self._mem.search(
                query_vector=req.query_vector,
                top_k=req.top_k,
                score_threshold=req.score_threshold,
            )
        return SearchResponse(
            hits=[self._to_response(h) for h in hits],
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )

    # ---- goal updates ---------------------------------------------------

    def update_goal(self, goal_embedding: list[float]) -> None:
        with self._span("update_goal", dim=len(goal_embedding)):
            self._pruner.set_goal(goal_embedding)

    # ---- operational ----------------------------------------------------

    async def health(self) -> HealthResponse:
        l1 = len(await self._mem.cache.scan())
        l2 = len(await self._mem.vector.scan())
        l3 = await self._mem.archive.count()

        back_pressure = self._back_pressure_active()
        return HealthResponse(
            status="degraded" if back_pressure else "ok",
            l1_size=l1,
            l2_size=l2,
            l3_size=l3,
            prune_lag_seconds=self._prune_lag_seconds(),
            writes_since_prune=self._writes_since_prune,
            back_pressure=back_pressure,
        )

    def last_prune(self) -> PruneStatsResponse | None:
        return self._last_stats

    # ---- pruner schedule ------------------------------------------------

    async def _schedule_loop(self) -> None:
        """Own the cadence instead of letting the pruner self-schedule.

        That way every cycle passes through ``_run_cycle`` where we can
        timestamp completion, update back-pressure counters, and emit
        Logfire metrics in one place.
        """
        while not self._stop.is_set():
            try:
                await self._run_cycle()
            except Exception:
                log.exception("prune cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._prune_interval)
            except asyncio.TimeoutError:
                pass

    async def _run_cycle(self) -> None:
        with self._span("prune_cycle"):
            stats = await self._pruner.run_once()

        self._last_prune_ts = time.monotonic()
        self._writes_since_prune = 0
        self._last_stats = PruneStatsResponse(
            scanned=stats.scanned,
            demoted_l1_l2=stats.demoted_l1_l2,
            demoted_l2_l3=stats.demoted_l2_l3,
            wall_ms=stats.wall_ms,
            started_at=stats.started_at,
        )
        log.info(
            "prune ok: demoted_l1_l2=%d demoted_l2_l3=%d wall_ms=%.1f",
            stats.demoted_l1_l2, stats.demoted_l2_l3, stats.wall_ms,
        )

    # ---- back-pressure --------------------------------------------------

    def _back_pressure_active(self) -> bool:
        if self._writes_since_prune > self._max_writes_gap:
            return True
        lag = self._prune_lag_seconds()
        return lag is not None and lag > self._max_prune_lag

    def _prune_lag_seconds(self) -> float | None:
        if self._last_prune_ts is None:
            return None
        return time.monotonic() - self._last_prune_ts

    # ---- projections & instrumentation ---------------------------------

    def _to_response(self, shard: MemoryShard) -> ShardResponse:
        return ShardResponse(
            id=shard.id,
            content=shard.content,
            tier=shard.tier,
            utility_score=shard.utility_score,
            access_count=shard.access_count,
            last_accessed_at=shard.last_accessed_at,
            goal_context=shard.goal_context,
        )

    @contextmanager
    def _span(self, name: str, **attrs) -> Iterator[None]:
        """Open a Logfire span if Logfire is installed; otherwise no-op.

        Kept as a single chokepoint so every read/write/prune op is traced
        the same way — callers never branch on whether Logfire is present.
        """
        if _logfire is None:
            yield
            return
        with _logfire.span(name, **attrs):
            yield
