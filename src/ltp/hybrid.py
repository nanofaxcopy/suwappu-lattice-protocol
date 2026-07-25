"""
Hybrid cryptographic migration for the Lattice Transfer Protocol.

Implements the xDSA-based composite signature scheme per IETF
draft-ietf-lamps-pq-composite-sigs: ML-DSA-65 + Ed25519-SHA512.

Design informed by crypto-rs (dark-bio/crypto-rs) xDSA implementation:
  - CompositeSignature: ML-DSA-65 (3309B) + Ed25519 (64B) = 3373B total
  - composite_signing_message(): M' = Prefix || Label || len(ctx) || ctx || SHA512(M)
  - split_signing_message(): Enables separate hardware signing workflows
    (HSM holds Ed25519 key, software holds ML-DSA key)
  - AlgorithmRegistry: Version-aware algorithm selection for transitions

Transition strategy:
  1. SignedEnvelope.version=1 uses ML-DSA-65 (current, pure PQ)
  2. SignedEnvelope.version=2 uses composite xDSA (ML-DSA-65 + Ed25519-SHA512)
  3. Verifiers accept both versions during migration window
  4. split_signing_message() enables HSM/software split signing

Key sizes (composite):
  SK: 64B (Ed25519) + 4032B (ML-DSA-65) = 4096B
  PK: 32B (Ed25519) + 1952B (ML-DSA-65) = 1984B
  Sig: 64B (Ed25519) + 3309B (ML-DSA-65) = 3373B

Reference: SUWAPPU_PRE_BLOCKCHAIN_ROADMAP.md §2.14
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from enum import Enum

from .domain import DOMAIN_SIGNED_ENVELOPE, domain_sign, domain_verify
from .primitives import MLDSA

_pynacl_available = False
try:
    from nacl.bindings import crypto_sign, crypto_sign_open
    from nacl.exceptions import CryptoError as _NaclCryptoError

    _pynacl_available = True
except ImportError:
    pass

__all__ = [
    "AlgorithmId",
    "CompositeSignature",
    "composite_signing_message",
    "split_signing_message",
    "generate_composite_keypair",
    "AlgorithmRegistry",
]

# IETF composite signature constants
_COMPOSITE_PREFIX = b"composite-sig-v1"
_COMPOSITE_LABEL = b"MLDSA65-Ed25519-SHA512"

# Ed25519 key/signature sizes (libsodium convention: the 64B "secret key"
# is seed(32) || pubkey(32) packed together, matching this module's
# documented composite SK/PK/Sig sizes above).
_ED25519_SK_SIZE = 64
_ED25519_PK_SIZE = 32
_ED25519_SIG_SIZE = 64


class AlgorithmId(Enum):
    """Supported signature algorithms.

    For post-quantum security, use MLDSA65 (pure ML-DSA-65).
    The composite MLDSA65_ED25519_SHA512 includes Ed25519 which is
    NOT post-quantum safe (broken by Shor's algorithm). It exists
    only for backward compatibility during migration.
    """

    MLDSA65 = "mldsa65"  # Pure PQ (recommended)
    MLDSA65_ED25519_SHA512 = "mldsa65-ed25519"  # Composite — Ed25519 NOT PQ-safe


@dataclass
class CompositeSignature:
    """ML-DSA-65 + Ed25519-SHA512 composite signature per IETF draft.

    Contains both a post-quantum and a classical signature. Both must
    verify for the composite to be valid.

    Fields:
        ml_sig: ML-DSA-65 signature (3309 bytes)
        ed_sig: Ed25519 signature (64 bytes)
    """

    ml_sig: bytes  # 3309B ML-DSA-65
    ed_sig: bytes  # 64B Ed25519

    _ML_SIG_SIZE = 3309
    _ED_SIG_SIZE = 64
    TOTAL_SIZE = _ML_SIG_SIZE + _ED_SIG_SIZE  # 3373B

    def to_bytes(self) -> bytes:
        """Concatenate ML-DSA + Ed25519 signatures."""
        return self.ml_sig + self.ed_sig

    @classmethod
    def from_bytes(cls, data: bytes) -> "CompositeSignature":
        """Parse a composite signature from concatenated bytes."""
        if len(data) != cls.TOTAL_SIZE:
            raise ValueError(f"composite signature must be {cls.TOTAL_SIZE}B, got {len(data)}")
        return cls(
            ml_sig=data[: cls._ML_SIG_SIZE],
            ed_sig=data[cls._ML_SIG_SIZE :],
        )


def composite_signing_message(message: bytes, context: bytes = b"") -> bytes:
    """Construct the composite signing message M'.

    M' = Prefix || Label || len(ctx) || ctx || SHA512(M)

    Mirrors crypto-rs split_signing_message(). The SHA512 pre-hash ensures
    that both ML-DSA and Ed25519 sign a fixed-size input regardless of
    message length.

    Args:
        message: The message to sign
        context: Optional context string (default empty)

    Returns:
        The composite signing message M'
    """
    prehash = hashlib.sha512(message).digest()
    return (
        _COMPOSITE_PREFIX + _COMPOSITE_LABEL + struct.pack(">H", len(context)) + context + prehash
    )


def split_signing_message(message: bytes, context: bytes = b"") -> tuple[bytes, bytes]:
    """Split a message into ML-DSA and Ed25519 signing inputs.

    Enables separate hardware signing workflows where an HSM holds the
    Ed25519 key and software holds the ML-DSA key.

    Both components sign the same composite message M', ensuring
    domain separation and binding.

    Args:
        message: The message to sign
        context: Optional context string

    Returns:
        (ml_message, ed_message) — both are the composite M'
    """
    m_prime = composite_signing_message(message, context)
    # Both algorithms sign the same M' (with their own domain separation)
    return m_prime, m_prime


def generate_composite_keypair() -> tuple[bytes, bytes]:
    """Generate a real composite (Ed25519 + ML-DSA-65) keypair.

    Returns:
        (vk, sk) where vk = 32B Ed25519 pk || 1952B ML-DSA-65 pk (1984B),
        sk = 64B Ed25519 sk || 4032B ML-DSA-65 sk (4096B) — the exact
        layout `AlgorithmRegistry.sign`/`.verify` expect for
        `AlgorithmId.MLDSA65_ED25519_SHA512`.
    """
    if not _pynacl_available:
        raise RuntimeError(
            "generate_composite_keypair requires pynacl "
            "(pip install 'ltp[crypto]' or 'ltp[dev]')."
        )
    from nacl.bindings import crypto_sign_keypair

    ed_pk, ed_sk = crypto_sign_keypair()
    mldsa_vk, mldsa_sk = MLDSA.keygen()
    return ed_pk + mldsa_vk, ed_sk + mldsa_sk


class AlgorithmRegistry:
    """Version-aware algorithm selection for signature transitions.

    Manages the transition from pure ML-DSA-65 (version 1) to composite
    ML-DSA-65 + Ed25519-SHA512 (version 2). Verifiers accept both
    versions during the migration window.
    """

    def __init__(self) -> None:
        self._supported: dict[AlgorithmId, bool] = {
            AlgorithmId.MLDSA65: True,
            AlgorithmId.MLDSA65_ED25519_SHA512: True,
        }

    def sign(
        self,
        algo_id: AlgorithmId,
        sk: bytes,
        message: bytes,
        domain: bytes,
    ) -> bytes:
        """Sign a message using the specified algorithm.

        For MLDSA65: returns a 3309B ML-DSA-65 signature.
        For MLDSA65_ED25519_SHA512: returns a 3373B composite signature —
            a REAL Ed25519 signature (via pynacl/libsodium) concatenated
            with a real ML-DSA-65 signature, both over the same composite
            message M'. Both components must independently verify.

        Args:
            algo_id: Algorithm to use
            sk:      Signing key. For MLDSA65: the 4032B ML-DSA-65 sk.
                     For MLDSA65_ED25519_SHA512: 4096B composite —
                     64B Ed25519 sk || 4032B ML-DSA-65 sk (see module
                     docstring "Key sizes (composite)").
            message: Message to sign
            domain:  Domain separation tag
        """
        if not self._supported.get(algo_id, False):
            raise ValueError(f"unsupported algorithm: {algo_id.value}")

        if algo_id == AlgorithmId.MLDSA65:
            return domain_sign(domain, sk, message)

        elif algo_id == AlgorithmId.MLDSA65_ED25519_SHA512:
            import warnings

            warnings.warn(
                "Composite signature (MLDSA65_ED25519_SHA512) includes Ed25519 "
                "which is NOT post-quantum safe. Use AlgorithmId.MLDSA65 for PQ security.",
                stacklevel=2,
            )
            if not _pynacl_available:
                raise RuntimeError(
                    "MLDSA65_ED25519_SHA512 requires pynacl for real Ed25519 signing "
                    "(pip install 'ltp[crypto]' or 'ltp[dev]')."
                )
            expected_sk_len = _ED25519_SK_SIZE + 4032
            if len(sk) != expected_sk_len:
                raise ValueError(
                    f"composite sk must be {expected_sk_len}B "
                    f"({_ED25519_SK_SIZE}B Ed25519 + 4032B ML-DSA-65), got {len(sk)}"
                )
            ed_sk, mldsa_sk = sk[:_ED25519_SK_SIZE], sk[_ED25519_SK_SIZE:]

            m_prime = composite_signing_message(message)
            ml_sig = domain_sign(domain, mldsa_sk, m_prime)
            # crypto_sign() is libsodium's attached-mode signing: it
            # returns sig(64B) || message. Detached signing isn't exposed
            # at the nacl.bindings level, so we slice the signature off.
            ed_signed = crypto_sign(m_prime, ed_sk)
            ed_sig = ed_signed[:_ED25519_SIG_SIZE]
            composite = CompositeSignature(ml_sig=ml_sig, ed_sig=ed_sig)
            return composite.to_bytes()

        raise ValueError(f"unknown algorithm: {algo_id.value}")

    def verify(
        self,
        algo_id: AlgorithmId,
        vk: bytes,
        message: bytes,
        domain: bytes,
        sig: bytes,
    ) -> bool:
        """Verify a signature using the specified algorithm.

        For MLDSA65: verifies a standard ML-DSA-65 signature.
        For MLDSA65_ED25519_SHA512: verifies BOTH components of the
            composite — a real Ed25519 verify (via pynacl/libsodium) AND
            a real ML-DSA-65 verify. Both must pass; this is the whole
            point of a composite/hybrid signature (defense in depth if
            either algorithm is later broken).

        Args:
            algo_id: Algorithm used for signing
            vk:      Verification key. For MLDSA65: the 1952B ML-DSA-65
                     pk. For MLDSA65_ED25519_SHA512: 1984B composite —
                     32B Ed25519 pk || 1952B ML-DSA-65 pk.
            message: Original message
            domain:  Domain separation tag
            sig:     Signature bytes
        """
        if not self._supported.get(algo_id, False):
            return False

        if algo_id == AlgorithmId.MLDSA65:
            return domain_verify(domain, vk, message, sig)

        elif algo_id == AlgorithmId.MLDSA65_ED25519_SHA512:
            if not _pynacl_available:
                raise RuntimeError(
                    "MLDSA65_ED25519_SHA512 requires pynacl for real Ed25519 verification "
                    "(pip install 'ltp[crypto]' or 'ltp[dev]')."
                )
            expected_vk_len = _ED25519_PK_SIZE + 1952
            if len(vk) != expected_vk_len:
                return False
            ed_pk, mldsa_pk = vk[:_ED25519_PK_SIZE], vk[_ED25519_PK_SIZE:]

            try:
                composite = CompositeSignature.from_bytes(sig)
            except ValueError:
                return False
            m_prime = composite_signing_message(message)

            # Both components must independently verify.
            if not domain_verify(domain, mldsa_pk, m_prime, composite.ml_sig):
                return False
            try:
                opened = crypto_sign_open(composite.ed_sig + m_prime, ed_pk)
            except _NaclCryptoError:
                return False
            return opened == m_prime

        return False

    def supported_algorithms(self) -> list[AlgorithmId]:
        """Return list of supported algorithm IDs."""
        return [a for a, enabled in self._supported.items() if enabled]
