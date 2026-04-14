"""
ZK proof package for the Lattice Transfer Protocol.

Provides real elliptic curve Pedersen commitments and Sigma protocol
zero-knowledge proofs on BLS12-381 G1.
"""

from .ec_backend import bls12_381_available

__all__ = ["bls12_381_available"]
