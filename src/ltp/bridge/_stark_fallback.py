"""Shared STARK fallback for zkVM provers in mock mode.

Both ``SP1ZKBridgeProver`` and ``RISC0ZKBridgeProver`` offer a "mock" prove
mode used when the native Rust toolchain is unavailable. Rather than emit a
fake hash-based proof, both now delegate to the real ``STARKBridgeProver``
which produces a FRI-based polynomial commitment with 128-bit soundness.

This module factors out that shared delegation so SP1 and RISC Zero don't
carry copy-pasted implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .zk_bridge import STARKBridgeProver

if TYPE_CHECKING:
    from ..merkle_log.sth import SignedTreeHead

__all__ = ["stark_fallback_proof_bytes"]


def stark_fallback_proof_bytes(sth: "SignedTreeHead") -> bytes:
    """Return raw STARK proof bytes for ``sth`` using default security params.

    128-bit soundness, 4 FRI rounds. The returned bytes carry no backend
    discriminator on their own; callers re-wrap them in a ``ZKBridgeProof``
    with their own backend tag (SP1 / RISC_ZERO).
    """
    prover = STARKBridgeProver(num_fri_rounds=4, security_bits=128)
    return prover.prove_sth_signature(sth).proof_bytes
