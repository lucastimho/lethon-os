"""Cryptographic attestation + defensive middleware for Lethon-OS.

Public surface, by concern:

**Signing / verification**
    :class:`Ed25519Signer`, :class:`Ed25519Verifier`,
    :func:`sign_shard`, :func:`verify_shard`,
    :func:`sign_receipt`, :func:`verify_receipt`,
    :class:`IntegrityError`

**Key management**
    :class:`KeyRegistry` — rotation-aware lookup by ``key_id``

**Audit trail**
    :class:`SignedAuditLog` — append-only SQLite log with chain replay,
    :class:`ChainError`

**Adversarial defense**
    :class:`MemoryScrubber`, :class:`ScrubDecision`, :class:`Severity`,
    :class:`ScrubberAlert`

**Integration**
    :class:`SecureMemoryController` — composed facade over the base
    :class:`MemoryController`
"""

from lethon_os.security.audit_log import ChainError, SignedAuditLog
from lethon_os.security.keys import KeyRegistry
from lethon_os.security.schemas import (
    AuditAction,
    SignedAuditReceipt,
    VerifiedMemoryShard,
)
from lethon_os.security.scrubber import (
    MemoryScrubber,
    ScrubDecision,
    ScrubberAlert,
    SemanticScorer,
    Severity,
)
from lethon_os.security.secure_controller import SecureMemoryController
from lethon_os.security.signing import (
    Ed25519Signer,
    Ed25519Verifier,
    IntegrityError,
    sign_receipt,
    sign_shard,
    verify_receipt,
    verify_shard,
)

__all__ = [
    "AuditAction",
    "ChainError",
    "Ed25519Signer",
    "Ed25519Verifier",
    "IntegrityError",
    "KeyRegistry",
    "MemoryScrubber",
    "ScrubDecision",
    "ScrubberAlert",
    "SecureMemoryController",
    "SemanticScorer",
    "Severity",
    "SignedAuditLog",
    "SignedAuditReceipt",
    "VerifiedMemoryShard",
    "sign_receipt",
    "sign_shard",
    "verify_receipt",
    "verify_shard",
]
