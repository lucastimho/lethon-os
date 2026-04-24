"""KeyRegistry — rotation + impersonation defense."""

from __future__ import annotations

import pytest

from lethon_os.security import (
    Ed25519Signer,
    IntegrityError,
    KeyRegistry,
)


def test_register_and_get():
    reg = KeyRegistry()
    signer = Ed25519Signer.generate("pruner-a")
    reg.register(signer.verifier())

    assert "pruner-a" in reg
    assert len(reg) == 1
    assert reg.get("pruner-a").key_id == "pruner-a"


def test_unknown_key_id_raises():
    reg = KeyRegistry()
    with pytest.raises(IntegrityError, match="unknown key_id"):
        reg.get("nonexistent")


def test_idempotent_register_is_noop():
    reg = KeyRegistry()
    signer = Ed25519Signer.from_seed(b"\x01" * 32, "stable")
    v1 = signer.verifier()
    v2 = signer.verifier()  # same underlying key, distinct Verifier instance

    reg.register(v1)
    reg.register(v2)  # must not raise

    assert len(reg) == 1


def test_impersonation_attempt_rejected():
    """Two distinct key-pairs trying to share a key_id must be caught."""
    reg = KeyRegistry()
    legit = Ed25519Signer.generate("pruner-a")
    impostor = Ed25519Signer.generate("pruner-a")  # same id, different key

    reg.register(legit.verifier())

    with pytest.raises(IntegrityError, match="impersonation|different public key"):
        reg.register(impostor.verifier())


def test_rotation_keeps_old_keys_usable():
    """Retiring a key by minting a new id with a new key must not remove
    the old entry — historical receipts stay verifiable."""
    reg = KeyRegistry()
    reg.register(Ed25519Signer.generate("pruner-2025").verifier())
    reg.register(Ed25519Signer.generate("pruner-2026").verifier())

    assert reg.known_ids() == ["pruner-2025", "pruner-2026"]
    assert len(reg) == 2
