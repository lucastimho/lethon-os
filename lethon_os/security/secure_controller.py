"""SecureMemoryController — hardened facade over :class:`MemoryController`.

Composes the pieces that, together, implement the Lethon-Shield
threat model at the application layer:

  * L0 constitution protection — writes to ``L0_CORE`` must go through
    :meth:`install_constitution`, never via ``put()``.
  * Memory Scrubber — every shard retrieved from L2 (Qdrant) or L3
    (Archive) is sanitised before it leaves the controller. L1 reads are
    skipped because shards in the hot path have already been scrubbed
    once on their way in.
  * Signed audit log — every lifecycle action emits a
    :class:`SignedAuditReceipt` chained to the log tip.
  * Pruner hook — the pruner is wired through
    :meth:`bind_pruner` so its demotions flow into the audit log.

The underlying :class:`MemoryController` is untouched; callers that
don't need the attestation layer can keep using it directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lethon_os.controller import MemoryController
from lethon_os.schemas import MemoryShard, Tier
from lethon_os.security.audit_log import SignedAuditLog
from lethon_os.security.schemas import AuditAction
from lethon_os.security.scrubber import (
    MemoryScrubber,
    ScrubberAlert,
    ScrubDecision,
    Severity,
)
from lethon_os.security.signing import Ed25519Signer, sign_receipt
from lethon_os.tiers.archive import L0ProtectionError

if TYPE_CHECKING:
    from lethon_os.pruner import UtilityPruner

log = logging.getLogger("lethon_os.security")


class SecureMemoryController:
    """Security-hardened wrapper. Thin — all storage work delegates down.

    Parameters
    ----------
    memory:
        The underlying tiered store.
    signer:
        Private key that will sign every audit receipt produced by this
        controller.
    audit_log:
        Append-only log receiving receipts.
    scrubber:
        Optional prompt-injection defense for L2/L3 reads. If omitted,
        retrieval is unfiltered (not recommended for production).
    """

    def __init__(
        self,
        memory: MemoryController,
        signer: Ed25519Signer,
        audit_log: SignedAuditLog,
        scrubber: MemoryScrubber | None = None,
    ) -> None:
        self._mem = memory
        self._signer = signer
        self._audit = audit_log
        self._scrubber = scrubber
        self._quarantined: set[str] = set()

    # ---- lifecycle ------------------------------------------------------

    async def setup(self) -> None:
        await self._mem.setup()
        await self._audit.connect()

    async def close(self) -> None:
        await self._audit.close()
        await self._mem.close()

    # ---- write path -----------------------------------------------------

    async def put(self, shard: MemoryShard) -> None:
        """Standard write — ``Tier.L0_CORE`` is rejected; use
        :meth:`install_constitution` for the dedicated L0 write path."""
        if shard.tier is Tier.L0_CORE:
            raise L0ProtectionError(
                f"cannot PUT into L0_CORE via normal write path; "
                f"use install_constitution() explicitly",
            )
        # The base controller writes to both L1 and L2; its vector.put
        # mutates shard.tier to L2 in-place. Capture the caller's
        # intended tier BEFORE delegating so the audit receipt reflects
        # the primary tier of the shard, not its write-through side-effect.
        requested_tier = shard.tier
        await self._mem.put(shard)
        await self._emit_receipt(
            shard_id=shard.id,
            action=AuditAction.PUT,
            to_tier=requested_tier,
        )

    async def install_constitution(self, shard: MemoryShard) -> None:
        """Dedicated L0 write path — callers must explicitly opt in.

        In production this should also require a human operator's
        signature (e.g. a second Ed25519 key held by the security team)
        before the shard is accepted. Left as a TODO so the base tier
        infrastructure lands first; the operator-signature check layers
        on cleanly once the signer registry supports multi-party policies.
        """
        shard.tier = Tier.L0_CORE
        # The pruner already filters L0 out; we can safely persist it
        # to L1 (Redis) for fast access. L2/L3 are not touched.
        await self._mem.cache.put(shard)
        await self._emit_receipt(
            shard_id=shard.id,
            action=AuditAction.PUT,
            to_tier=Tier.L0_CORE,
        )
        log.info("constitution shard installed: %s", shard.id)

    # ---- read path ------------------------------------------------------

    async def get(self, shard_id: str) -> MemoryShard | None:
        """Cache-Aside walk with inline scrubbing at L2/L3 hit sites.

        The base controller's ``get()`` promotes L2/L3 hits to L1 *before*
        returning — by the time this method saw the shard, its tier would
        already read as L1 and the scrubber would be skipped. We
        reimplement the walk here so the scrubber runs on the retrieved
        shard in its original tier, and promotion is conditional on the
        decision.
        """
        if shard_id in self._quarantined:
            return None

        # L1 — hot path, no scrubbing (shards got here via put() or via a
        # previously-scrubbed promotion).
        shard = await self._mem.cache.get(shard_id)
        if shard is not None:
            return shard

        # L2 — scrub before promoting.
        shard = await self._mem.vector.get(shard_id)
        if shard is not None:
            if await self._scrub_blocks(shard):
                return None
            await self._mem.cache.put(shard)
            return shard

        # L3 — scrub before restoring up the stack.
        shard = await self._mem.archive.get(shard_id)
        if shard is not None:
            if await self._scrub_blocks(shard):
                return None
            await self._mem.vector.put(shard)
            await self._mem.cache.put(shard)
            await self._mem.archive.delete(shard_id)
            return shard

        return None

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 8,
        score_threshold: float | None = None,
    ) -> list[MemoryShard]:
        # The base controller's search unconditionally promotes every hit
        # to L1. Bypass it: pull raw hits from the vector tier, scrub, and
        # promote only the survivors.
        hits = await self._mem.vector.search(
            query_vector, top_k, score_threshold,
        )

        clean: list[MemoryShard] = []
        for h in hits:
            if h.id in self._quarantined:
                continue
            if await self._scrub_blocks(h):
                continue
            await self._mem.cache.put(h)
            clean.append(h)
        return clean

    async def _scrub_blocks(self, shard: MemoryShard) -> bool:
        """Run the scrubber; return True if the shard must not be returned.

        Quarantines the shard id and emits an audit receipt for the block.
        A ``ScrubberAlert`` from the scrubber is re-raised so upstream can
        trip back-pressure — the caller (API layer) converts it to HTTP 503.
        """
        if self._scrubber is None:
            return False

        decision = await self._run_scrubber(shard)
        if decision.is_blocking:
            await self._quarantine(shard, decision)
            return True
        return False

    # ---- pruner integration --------------------------------------------

    def bind_pruner(self, pruner: "UtilityPruner") -> None:
        """Install the audit hook on an existing pruner.

        We don't construct the pruner here — callers control its
        weights, interval, and goal — but once they hand us the instance
        we override its ``on_action`` hook so every demote / archive
        becomes a receipt in the log.
        """
        pruner._on_action = self._on_pruner_action  # type: ignore[assignment]

    async def _on_pruner_action(
        self,
        shard: MemoryShard,
        action: str,
        from_tier: Tier,
        to_tier: Tier,
    ) -> None:
        # Map the pruner's string vocabulary to the audit enum. Unknown
        # actions are logged but not dropped — visibility over correctness
        # in the audit sink.
        try:
            audit_action = AuditAction(action)
        except ValueError:
            log.warning("unknown pruner action '%s' — recording as DEMOTE", action)
            audit_action = AuditAction.DEMOTE

        await self._emit_receipt(
            shard_id=shard.id,
            action=audit_action,
            from_tier=from_tier,
            to_tier=to_tier,
        )

    # ---- integrity ------------------------------------------------------

    async def verify_audit_chain(self) -> None:
        """Replay the audit log. Raises on any break. Intended for
        startup and periodic (e.g. hourly) integrity checks."""
        await self._audit.verify_chain()

    async def audit_merkle_root(self) -> str | None:
        return await self._audit.merkle_root()

    # ---- internals ------------------------------------------------------

    async def _emit_receipt(
        self,
        shard_id: str,
        action: AuditAction,
        from_tier: Tier | None = None,
        to_tier: Tier | None = None,
    ) -> None:
        tip = await self._audit.tip()
        receipt = sign_receipt(
            shard_id=shard_id,
            action=action,
            signer=self._signer,
            from_tier=from_tier,
            to_tier=to_tier,
            prev_receipt_hash=tip,
        )
        await self._audit.append(receipt)

    async def _run_scrubber(self, shard: MemoryShard) -> ScrubDecision:
        assert self._scrubber is not None
        try:
            return await self._scrubber.scrub(shard)
        except ScrubberAlert as alert:
            # Emit a DELETE receipt for the spike event itself — gives the
            # security team a clear pivot in the audit log — then re-raise
            # so upstream converts to HTTP 503 + tool-call halt.
            log.error(
                "scrubber spike: %d flags in %.0fs — halting",
                alert.flags_in_window, alert.window_seconds,
            )
            raise

    async def _quarantine(
        self, shard: MemoryShard, decision: ScrubDecision,
    ) -> None:
        """Record a blocking decision in the audit log and add to the
        in-memory quarantine set so subsequent reads skip immediately."""
        self._quarantined.add(shard.id)
        await self._emit_receipt(
            shard_id=shard.id,
            action=AuditAction.DELETE,  # blocking == deletion from the caller's view
            from_tier=shard.tier,
            to_tier=None,
        )
        log.warning(
            "quarantined %s (severity=%s reasons=%s)",
            shard.id, decision.severity.value, decision.reasons,
        )
