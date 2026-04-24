"""SignedAuditLog — chain integrity, tamper detection, Merkle root."""

from __future__ import annotations

import pytest
import pytest_asyncio

from lethon_os.schemas import Tier
from lethon_os.security import (
    AuditAction,
    ChainError,
    Ed25519Signer,
    IntegrityError,
    KeyRegistry,
    SignedAuditLog,
    sign_receipt,
)


@pytest_asyncio.fixture
async def signer_and_log(tmp_path):
    signer = Ed25519Signer.from_seed(b"\x02" * 32, "pruner-a")
    reg = KeyRegistry()
    reg.register(signer.verifier())

    log = SignedAuditLog(db_path=":memory:", keys=reg)
    await log.connect()
    yield signer, log
    await log.close()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_append_and_fetch_roundtrip(signer_and_log):
    signer, log = signer_and_log
    receipt = sign_receipt(
        "shard-1", AuditAction.PUT, signer, to_tier=Tier.L1,
    )
    await log.append(receipt)

    assert await log.count() == 1
    stored = await log.for_shard("shard-1")
    assert len(stored) == 1
    assert stored[0].receipt_hash == receipt.receipt_hash


async def test_chain_links_across_multiple_receipts(signer_and_log):
    signer, log = signer_and_log

    r1 = sign_receipt("shard-1", AuditAction.PUT, signer, to_tier=Tier.L1)
    await log.append(r1)

    tip = await log.tip()
    r2 = sign_receipt(
        "shard-1", AuditAction.DEMOTE, signer,
        from_tier=Tier.L1, to_tier=Tier.L2,
        prev_receipt_hash=tip,
    )
    await log.append(r2)

    r3 = sign_receipt(
        "shard-1", AuditAction.ARCHIVE, signer,
        from_tier=Tier.L2, to_tier=Tier.L3,
        prev_receipt_hash=await log.tip(),
    )
    await log.append(r3)

    assert await log.count() == 3
    await log.verify_chain()  # full replay succeeds


# ---------------------------------------------------------------------------
# Chain-break detection
# ---------------------------------------------------------------------------


async def test_append_rejects_wrong_prev_hash(signer_and_log):
    signer, log = signer_and_log
    await log.append(
        sign_receipt("shard-1", AuditAction.PUT, signer, to_tier=Tier.L1),
    )

    # Forge a receipt that claims to chain from a hash the log never saw.
    forged = sign_receipt(
        "shard-1", AuditAction.DEMOTE, signer,
        from_tier=Tier.L1, to_tier=Tier.L2,
        prev_receipt_hash="0" * 64,  # fake predecessor
    )

    with pytest.raises(ChainError, match="chain tip mismatch"):
        await log.append(forged)


async def test_append_rejects_unknown_signer(tmp_path):
    """Receipts from unregistered keys can't reach the log."""
    reg = KeyRegistry()  # empty
    log = SignedAuditLog(db_path=":memory:", keys=reg)
    await log.connect()

    outsider = Ed25519Signer.generate("outsider")
    receipt = sign_receipt("shard-1", AuditAction.PUT, outsider, to_tier=Tier.L1)

    with pytest.raises(IntegrityError, match="unknown key_id"):
        await log.append(receipt)

    await log.close()


async def test_verify_chain_catches_mutated_payload(signer_and_log):
    signer, log = signer_and_log

    r1 = sign_receipt("shard-1", AuditAction.PUT, signer, to_tier=Tier.L1)
    await log.append(r1)
    r2 = sign_receipt(
        "shard-1", AuditAction.DEMOTE, signer,
        from_tier=Tier.L1, to_tier=Tier.L2,
        prev_receipt_hash=await log.tip(),
    )
    await log.append(r2)

    # Attacker slips into the DB and flips the shard_id on receipt 2.
    await log._conn().execute(  # noqa: SLF001 — test-only escape hatch
        "UPDATE audit_log SET payload_json = REPLACE(payload_json, 'shard-1', 'shard-x') "
        "WHERE receipt_hash = ?",
        (r2.receipt_hash,),
    )
    await log._conn().commit()

    with pytest.raises(IntegrityError):
        await log.verify_chain()


# ---------------------------------------------------------------------------
# Merkle root
# ---------------------------------------------------------------------------


async def test_merkle_root_empty_log(signer_and_log):
    _, log = signer_and_log
    assert await log.merkle_root() is None


async def test_merkle_root_single_receipt_equals_its_hash(signer_and_log):
    signer, log = signer_and_log
    receipt = sign_receipt("s", AuditAction.PUT, signer, to_tier=Tier.L1)
    await log.append(receipt)

    root = await log.merkle_root()
    # With one leaf the root IS the leaf's hash.
    assert root == receipt.receipt_hash


async def test_merkle_root_changes_after_append(signer_and_log):
    signer, log = signer_and_log
    await log.append(
        sign_receipt("s1", AuditAction.PUT, signer, to_tier=Tier.L1),
    )
    root1 = await log.merkle_root()

    await log.append(
        sign_receipt(
            "s2", AuditAction.PUT, signer, to_tier=Tier.L1,
            prev_receipt_hash=await log.tip(),
        ),
    )
    root2 = await log.merkle_root()

    assert root1 != root2  # adding a receipt must move the root
