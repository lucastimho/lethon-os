"""Ed25519 signing service and canonicalisation helpers.

Ed25519 is the right primitive here: ~70k signatures/sec on commodity
hardware, 64-byte signatures, and zero key-generation ceremony — it fits
into the pruner's hot path without becoming the bottleneck the rest of
the system was engineered to avoid.

Verification is separated into :class:`Ed25519Verifier` so public keys
can be distributed to untrusted readers (dashboards, replay tools)
without exposing private key material.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from lethon_os.schemas import MemoryShard, Tier
from lethon_os.security.schemas import (
    AuditAction,
    SignedAuditReceipt,
    VerifiedMemoryShard,
)


class IntegrityError(Exception):
    """Raised on any canonicalisation, hash, or signature failure.

    The caller should treat this as a hard security event — never retry,
    never silently accept the payload. In the audit pipeline it maps to
    immediate back-pressure + human escalation.
    """


# ---------------------------------------------------------------------------
# Key wrappers
# ---------------------------------------------------------------------------


class Ed25519Signer:
    """Private-key holder with a stable ``key_id`` attached to every signature.

    Rotate keys by minting a new signer with a new ``key_id``; old receipts
    remain verifiable against the retired public key. Never reuse a
    ``key_id`` across distinct keys — the verifier uses it to route, so
    collisions silently validate against the wrong key.
    """

    __slots__ = ("_sk", "key_id")

    def __init__(self, private_key: Ed25519PrivateKey, key_id: str):
        if not key_id:
            raise ValueError("key_id must be a non-empty string")
        self._sk = private_key
        self.key_id = key_id

    # ---- construction ---------------------------------------------------

    @classmethod
    def generate(cls, key_id: str) -> "Ed25519Signer":
        """Fresh random key. Call once per agent/service and persist via
        :meth:`export_pem`; never regenerate on every restart."""
        return cls(Ed25519PrivateKey.generate(), key_id)

    @classmethod
    def from_seed(cls, seed: bytes, key_id: str) -> "Ed25519Signer":
        """Deterministic construction from a 32-byte seed.

        Intended for reproducible tests. Never hard-code a seed in
        production — the seed is the private key material.
        """
        if len(seed) != 32:
            raise ValueError("Ed25519 seed must be exactly 32 bytes")
        return cls(Ed25519PrivateKey.from_private_bytes(seed), key_id)

    @classmethod
    def load_pem(
        cls,
        path: Path | str,
        key_id: str,
        password: bytes | None = None,
    ) -> "Ed25519Signer":
        data = Path(path).read_bytes()
        key = serialization.load_pem_private_key(data, password=password)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"Expected Ed25519 key, got {type(key).__name__}")
        return cls(key, key_id)

    # ---- use ------------------------------------------------------------

    def sign(self, payload: bytes) -> bytes:
        return self._sk.sign(payload)

    def public_key_bytes(self) -> bytes:
        return self._sk.public_key().public_bytes_raw()

    def public_key_b64(self) -> str:
        return base64.b64encode(self.public_key_bytes()).decode("ascii")

    def verifier(self) -> "Ed25519Verifier":
        """Convenience: derive a matching :class:`Ed25519Verifier` — useful
        for local round-trip checks without shipping the private key."""
        return Ed25519Verifier(self._sk.public_key(), self.key_id)

    def export_pem(self, password: bytes | None = None) -> bytes:
        enc = (
            serialization.BestAvailableEncryption(password)
            if password
            else serialization.NoEncryption()
        )
        return self._sk.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=enc,
        )


class Ed25519Verifier:
    """Public-key verifier. Safe to hand to untrusted readers."""

    __slots__ = ("_pk", "key_id")

    def __init__(self, public_key: Ed25519PublicKey, key_id: str):
        self._pk = public_key
        self.key_id = key_id

    @classmethod
    def from_bytes(cls, raw: bytes, key_id: str) -> "Ed25519Verifier":
        return cls(Ed25519PublicKey.from_public_bytes(raw), key_id)

    @classmethod
    def from_b64(cls, b64: str, key_id: str) -> "Ed25519Verifier":
        return cls.from_bytes(base64.b64decode(b64), key_id)

    def public_key_bytes(self) -> bytes:
        """Raw 32-byte public key — used by :class:`KeyRegistry` to detect
        key_id collisions without exposing cryptography internals."""
        return self._pk.public_bytes_raw()

    def verify(self, signature: bytes, payload: bytes) -> None:
        """Raise :class:`IntegrityError` unless the signature is valid."""
        try:
            self._pk.verify(signature, payload)
        except InvalidSignature as e:
            raise IntegrityError(
                f"signature verification failed for key_id='{self.key_id}'"
            ) from e


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------


def _canonical(obj: Any) -> bytes:
    """Deterministic JSON bytes used as the signing input.

    Uses ``sort_keys=True`` and minimal separators so the byte output is
    stable across Python versions that share the same float repr. This is
    not full RFC 8785 (JCS) canonicalisation — floats remain at the mercy
    of Python's default repr — but it is sufficient given that our schema
    stores embeddings as lists of Python floats without any further
    precision contract.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("ascii")


def _hash(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=32).hexdigest()


def _shard_envelope(
    shard: MemoryShard,
    signed_by: str,
    signed_at: datetime,
    parent_hash: str | None,
) -> dict[str, Any]:
    return {
        "shard": shard.model_dump(mode="json"),
        "signed_by": signed_by,
        "signed_at": signed_at.isoformat(),
        "parent_hash": parent_hash,
    }


def _receipt_envelope(
    shard_id: str,
    action: AuditAction,
    actor_key_id: str,
    timestamp: datetime,
    from_tier: Tier | None,
    to_tier: Tier | None,
    prev_receipt_hash: str | None,
) -> dict[str, Any]:
    return {
        "shard_id": shard_id,
        "action": action.value,
        "from_tier": from_tier.value if from_tier else None,
        "to_tier": to_tier.value if to_tier else None,
        "actor_key_id": actor_key_id,
        "timestamp": timestamp.isoformat(),
        "prev_receipt_hash": prev_receipt_hash,
    }


# ---------------------------------------------------------------------------
# High-level sign / verify
# ---------------------------------------------------------------------------


def sign_shard(
    shard: MemoryShard,
    signer: Ed25519Signer,
    parent_hash: str | None = None,
    now: datetime | None = None,
) -> VerifiedMemoryShard:
    """Produce a :class:`VerifiedMemoryShard` bound to its chain position."""
    signed_at = now or datetime.now(timezone.utc)
    envelope = _shard_envelope(shard, signer.key_id, signed_at, parent_hash)
    payload = _canonical(envelope)
    signature = signer.sign(payload)
    return VerifiedMemoryShard(
        shard=shard,
        signed_by=signer.key_id,
        signed_at=signed_at,
        parent_hash=parent_hash,
        canonical_hash=_hash(payload),
        signature=base64.b64encode(signature).decode("ascii"),
    )


def verify_shard(
    verified: VerifiedMemoryShard,
    verifier: Ed25519Verifier,
) -> None:
    """Raise :class:`IntegrityError` unless every byte matches the signature."""
    if verified.signed_by != verifier.key_id:
        raise IntegrityError(
            f"key_id mismatch: shard signed by '{verified.signed_by}', "
            f"verifier is '{verifier.key_id}'"
        )
    envelope = _shard_envelope(
        verified.shard,
        verified.signed_by,
        verified.signed_at,
        verified.parent_hash,
    )
    payload = _canonical(envelope)
    if _hash(payload) != verified.canonical_hash:
        raise IntegrityError("canonical hash mismatch — shard body was modified")
    signature = base64.b64decode(verified.signature)
    verifier.verify(signature, payload)


def sign_receipt(
    shard_id: str,
    action: AuditAction,
    signer: Ed25519Signer,
    from_tier: Tier | None = None,
    to_tier: Tier | None = None,
    prev_receipt_hash: str | None = None,
    now: datetime | None = None,
) -> SignedAuditReceipt:
    """Emit a signed, chain-linked audit receipt for a lifecycle action."""
    timestamp = now or datetime.now(timezone.utc)
    envelope = _receipt_envelope(
        shard_id, action, signer.key_id, timestamp, from_tier, to_tier, prev_receipt_hash
    )
    payload = _canonical(envelope)
    signature = signer.sign(payload)
    return SignedAuditReceipt(
        shard_id=shard_id,
        action=action,
        from_tier=from_tier,
        to_tier=to_tier,
        actor_key_id=signer.key_id,
        timestamp=timestamp,
        prev_receipt_hash=prev_receipt_hash,
        receipt_hash=_hash(payload),
        signature=base64.b64encode(signature).decode("ascii"),
    )


def verify_receipt(
    receipt: SignedAuditReceipt,
    verifier: Ed25519Verifier,
) -> None:
    """Single-receipt integrity check. Chain-level replay is the caller's job."""
    if receipt.actor_key_id != verifier.key_id:
        raise IntegrityError(
            f"key_id mismatch: receipt from '{receipt.actor_key_id}', "
            f"verifier is '{verifier.key_id}'"
        )
    envelope = _receipt_envelope(
        receipt.shard_id,
        receipt.action,
        receipt.actor_key_id,
        receipt.timestamp,
        receipt.from_tier,
        receipt.to_tier,
        receipt.prev_receipt_hash,
    )
    payload = _canonical(envelope)
    if _hash(payload) != receipt.receipt_hash:
        raise IntegrityError("receipt hash mismatch — audit record was modified")
    signature = base64.b64decode(receipt.signature)
    verifier.verify(signature, payload)
