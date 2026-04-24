"""Pydantic schemas for the cryptographic attestation layer.

Kept separate from :mod:`lethon_os.schemas` so the core memory types have
zero dependency on ``cryptography``; callers that don't need the audit
trail can keep the base library lightweight.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from lethon_os.schemas import MemoryShard, Tier


class AuditAction(str, Enum):
    """Every state transition recorded to the audit log.

    ``PUT`` / ``PROMOTE`` / ``DEMOTE`` / ``ARCHIVE`` / ``DELETE`` cover the
    pruner's vocabulary; ``PIN`` / ``UNPIN`` are human-in-the-loop overrides
    that freeze a shard from utility decay.
    """

    PUT = "put"
    PROMOTE = "promote"   # hotter tier (L3→L2, L2→L1)
    DEMOTE = "demote"     # colder tier (L1→L2)
    ARCHIVE = "archive"   # → L3
    DELETE = "delete"     # permanent removal
    PIN = "pin"
    UNPIN = "unpin"


class VerifiedMemoryShard(BaseModel):
    """A :class:`MemoryShard` wrapped in an Ed25519 attestation.

    The signature covers the canonical JSON of ``shard`` together with the
    signer identity, timestamp, and ``parent_hash``. Mutating *any* field
    of the inner shard — or any wrapper field — invalidates the signature
    on :func:`verify_shard`.

    ``canonical_hash`` is stored redundantly so downstream audit tools can
    index shards by hash without re-canonicalising. Verifiers always
    recompute it from scratch, so a mismatch is caught.
    """

    model_config = ConfigDict(extra="forbid")

    shard: MemoryShard
    signed_by: str = Field(..., description="Key ID of the signer (rotatable).")
    signed_at: datetime
    parent_hash: str | None = Field(
        default=None,
        description="blake2b-256 hex of the previous verified shard in the chain.",
    )
    canonical_hash: str = Field(
        ...,
        description="blake2b-256 hex of the canonical signed envelope.",
    )
    signature: str = Field(..., description="Base64-encoded Ed25519 signature.")


class SignedAuditReceipt(BaseModel):
    """Non-repudiable record of a single memory-lifecycle action.

    Receipts are linked via ``prev_receipt_hash`` to form an append-only
    audit log: omitting or reordering any receipt is detectable by
    replaying the chain from genesis.

    The receipt's own canonical hash is stored in ``receipt_hash`` so the
    next receipt can bind to it without re-hashing.
    """

    model_config = ConfigDict(extra="forbid")

    shard_id: str
    action: AuditAction
    from_tier: Tier | None = None
    to_tier: Tier | None = None
    actor_key_id: str
    timestamp: datetime
    prev_receipt_hash: str | None = None
    receipt_hash: str
    signature: str
