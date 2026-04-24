"""Cryptographic attestation layer — non-repudiable memory governance."""

from lethon_os.security.keys import KeyRegistry
from lethon_os.security.schemas import (
    AuditAction,
    SignedAuditReceipt,
    VerifiedMemoryShard,
)
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
    "Ed25519Signer",
    "Ed25519Verifier",
    "IntegrityError",
    "KeyRegistry",
    "SignedAuditReceipt",
    "VerifiedMemoryShard",
    "sign_receipt",
    "sign_shard",
    "verify_receipt",
    "verify_shard",
]
