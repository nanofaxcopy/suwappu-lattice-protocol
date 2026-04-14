"""
Pedersen commitment scheme on BLS12-381 G1.

C = m*G + r*H

Where:
  - G is the standard BLS12-381 G1 generator
  - H is a nothing-up-my-sleeve generator (unknown DL relative to G)
  - m is the message scalar (derived from entity_id via SHA3-256)
  - r is the blinding scalar (derived from random blinding_factor)

Security properties:
  - Hiding: C reveals nothing about m (computational, under DLP)
  - Binding: Cannot open C to a different m (computational, under DLP)

WARNING: Not post-quantum safe. BLS12-381 DLP is broken by Shor's algorithm.
"""

from __future__ import annotations

import hmac

from .ec_backend import (
    g1_add,
    g1_generator,
    g1_h_generator,
    g1_scalar_mul,
    g1_serialize,
    scalar_from_bytes,
    scalar_from_entity_id,
)


def pedersen_commit(entity_id: str, blinding_factor: bytes) -> bytes:
    """
    Compute Pedersen commitment C = m*G + r*H on BLS12-381 G1.

    Args:
        entity_id: The entity identifier to commit to.
        blinding_factor: 32 bytes of randomness (the blinding factor r).

    Returns:
        96-byte serialized G1 point (the commitment C).
    """
    if not entity_id:
        raise ValueError("entity_id cannot be empty")
    if len(blinding_factor) < 16:
        raise ValueError("blinding_factor too short")
    m = scalar_from_entity_id(entity_id)
    r = scalar_from_bytes(blinding_factor)

    m_G = g1_scalar_mul(g1_generator(), m)
    r_H = g1_scalar_mul(g1_h_generator(), r)
    C = g1_add(m_G, r_H)

    return g1_serialize(C)


def pedersen_open(
    commitment_bytes: bytes,
    entity_id: str,
    blinding_factor: bytes,
) -> bool:
    """
    Verify that a commitment opens to (entity_id, blinding_factor).

    This is NOT zero-knowledge — it reveals entity_id. Used for
    dispute resolution or selective disclosure.

    Args:
        commitment_bytes: 96-byte serialized commitment point.
        entity_id: The claimed entity identifier.
        blinding_factor: The claimed blinding factor.

    Returns:
        True if C == m*G + r*H for the given (entity_id, blinding_factor).
    """
    if not entity_id:
        raise ValueError("entity_id cannot be empty")
    if len(blinding_factor) < 16:
        raise ValueError("blinding_factor too short")
    expected = pedersen_commit(entity_id, blinding_factor)
    return hmac.compare_digest(commitment_bytes, expected)
