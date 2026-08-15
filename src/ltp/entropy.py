"""
Centralized CSPRNG for the LTP SDK.

Single chokepoint for entropy so sourcing stays consistent and a future
HSM/DRBG migration is a one-module change (see the
raw-urandom-outside-cek-generation rule in .semgrep/key-handling.yml,
which rejects direct os.urandom() calls elsewhere in the SDK).

Stdlib-only and import-free within the package: safe to import from any
module, including primitives.py and bls.py, without cycles.
"""

from __future__ import annotations

import os

__all__ = ["secure_random_bytes"]


def secure_random_bytes(n: int) -> bytes:
    """Return ``n`` cryptographically secure random bytes.

    Currently backed by ``os.urandom`` (the OS CSPRNG). Key generation,
    nonces, salts, blinding factors, and padding all route through here.
    """
    if n < 0:
        raise ValueError(f"requested negative byte count: {n}")
    return os.urandom(n)
