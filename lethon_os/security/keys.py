"""Key registry — verifier lookup with rotation support.

Receipts and signed shards carry a ``key_id`` that names which public
key should validate them. The registry is the map from that id to the
actual :class:`Ed25519Verifier`. Retiring a signing key means minting a
new one with a new id; old receipts stay verifiable because the retired
public key stays in the registry.

Collision defense: registering a second verifier under an existing
``key_id`` with a *different* underlying public key raises
:class:`IntegrityError`. Idempotent re-registration of the same key is
a no-op. That closes the "attacker spoofs a known key_id to route their
own key" attack at registration time.
"""

from __future__ import annotations

from lethon_os.security.signing import Ed25519Verifier, IntegrityError


class KeyRegistry:
    """Thread-unsafe in-memory map of ``key_id → Ed25519Verifier``.

    For a multi-process deployment, wrap this in a Redis-backed store
    keyed by ``lethon:keys:<key_id>`` holding the base64 public bytes.
    The invariants are the same; only the persistence layer differs.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Ed25519Verifier] = {}

    def register(self, verifier: Ed25519Verifier) -> None:
        existing = self._by_id.get(verifier.key_id)
        if existing is None:
            self._by_id[verifier.key_id] = verifier
            return

        # Idempotent: re-registering the same public key is a no-op.
        if existing.public_key_bytes() == verifier.public_key_bytes():
            return

        raise IntegrityError(
            f"key_id '{verifier.key_id}' is already registered with a "
            f"different public key — possible impersonation",
        )

    def get(self, key_id: str) -> Ed25519Verifier:
        verifier = self._by_id.get(key_id)
        if verifier is None:
            raise IntegrityError(f"unknown key_id: '{key_id}'")
        return verifier

    def __contains__(self, key_id: object) -> bool:
        return key_id in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)

    def known_ids(self) -> list[str]:
        return list(self._by_id.keys())
