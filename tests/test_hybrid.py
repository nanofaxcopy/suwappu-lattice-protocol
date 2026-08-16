"""Tests for composite (ML-DSA-65 + Ed25519) signatures (hybrid.py).

Previously the Ed25519 half of the composite scheme was fake: `sign()`
wrote a SHA512 hash instead of a real Ed25519 signature (and leaked 32
bytes of the ML-DSA secret key into that hash), and `verify()` accepted
*any* 64-byte blob as a valid Ed25519 component. These tests prove the
real implementation actually enforces both halves.
"""

import pytest

pynacl = pytest.importorskip("nacl.bindings", reason="pynacl not installed (ltp[crypto]/ltp[dev])")

from src.ltp.domain import DOMAIN_SIGNED_ENVELOPE
from src.ltp.hybrid import AlgorithmId, AlgorithmRegistry, generate_composite_keypair


@pytest.fixture
def registry():
    return AlgorithmRegistry()


@pytest.fixture
def composite_keypair():
    return generate_composite_keypair()


def test_composite_sign_verify_round_trips(registry, composite_keypair):
    vk, sk = composite_keypair
    message = b"suwappu composite signature test"

    with pytest.warns(UserWarning, match="NOT post-quantum safe"):
        sig = registry.sign(AlgorithmId.MLDSA65_ED25519_SHA512, sk, message, DOMAIN_SIGNED_ENVELOPE)

    assert len(sig) == 3373  # 3309B ML-DSA-65 + 64B Ed25519
    assert registry.verify(
        AlgorithmId.MLDSA65_ED25519_SHA512, vk, message, DOMAIN_SIGNED_ENVELOPE, sig
    )


def test_composite_rejects_forged_ed25519_component(registry, composite_keypair):
    """The bug this replaces: verify() used to accept ANY 64-byte blob as
    the Ed25519 component, as long as the ML-DSA half was valid. A real
    composite signature must reject a signature whose Ed25519 half is
    garbage, even when the ML-DSA half is genuine."""
    vk, sk = composite_keypair
    message = b"suwappu composite signature test"

    with pytest.warns(UserWarning, match="NOT post-quantum safe"):
        sig = registry.sign(AlgorithmId.MLDSA65_ED25519_SHA512, sk, message, DOMAIN_SIGNED_ENVELOPE)

    # Swap in 64 bytes of garbage for the Ed25519 component; ML-DSA
    # component (the first 3309 bytes) is untouched and still genuine.
    forged = sig[:3309] + (b"\x00" * 64)
    assert not registry.verify(
        AlgorithmId.MLDSA65_ED25519_SHA512, vk, message, DOMAIN_SIGNED_ENVELOPE, forged
    )


def test_composite_rejects_tampered_message(registry, composite_keypair):
    vk, sk = composite_keypair
    message = b"original message"

    with pytest.warns(UserWarning, match="NOT post-quantum safe"):
        sig = registry.sign(AlgorithmId.MLDSA65_ED25519_SHA512, sk, message, DOMAIN_SIGNED_ENVELOPE)

    assert not registry.verify(
        AlgorithmId.MLDSA65_ED25519_SHA512, vk, b"tampered message", DOMAIN_SIGNED_ENVELOPE, sig
    )


def test_composite_rejects_wrong_key(registry, composite_keypair):
    vk, sk = composite_keypair
    other_vk, _other_sk = generate_composite_keypair()
    message = b"suwappu composite signature test"

    with pytest.warns(UserWarning, match="NOT post-quantum safe"):
        sig = registry.sign(AlgorithmId.MLDSA65_ED25519_SHA512, sk, message, DOMAIN_SIGNED_ENVELOPE)

    assert not registry.verify(
        AlgorithmId.MLDSA65_ED25519_SHA512, other_vk, message, DOMAIN_SIGNED_ENVELOPE, sig
    )


def test_composite_sign_rejects_wrong_sk_size(registry):
    with pytest.raises(ValueError, match="composite sk must be"):
        registry.sign(
            AlgorithmId.MLDSA65_ED25519_SHA512, b"\x00" * 10, b"msg", DOMAIN_SIGNED_ENVELOPE
        )


def test_composite_verify_rejects_wrong_vk_size(registry, composite_keypair):
    _vk, sk = composite_keypair
    message = b"msg"
    with pytest.warns(UserWarning, match="NOT post-quantum safe"):
        sig = registry.sign(AlgorithmId.MLDSA65_ED25519_SHA512, sk, message, DOMAIN_SIGNED_ENVELOPE)
    assert not registry.verify(
        AlgorithmId.MLDSA65_ED25519_SHA512, b"\x00" * 10, message, DOMAIN_SIGNED_ENVELOPE, sig
    )


def test_pure_mldsa_path_unaffected(registry):
    """AlgorithmId.MLDSA65 (the recommended, pure-PQ path) doesn't touch
    the Ed25519/pynacl code at all — confirm it still works standalone."""
    from src.ltp.primitives import MLDSA

    vk, sk = MLDSA.keygen()
    message = b"pure mldsa message"
    sig = registry.sign(AlgorithmId.MLDSA65, sk, message, DOMAIN_SIGNED_ENVELOPE)
    assert registry.verify(AlgorithmId.MLDSA65, vk, message, DOMAIN_SIGNED_ENVELOPE, sig)
