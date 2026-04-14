"""
Goldilocks field arithmetic and NTT for STARK proofs.

The Goldilocks prime p = 2^64 - 2^32 + 1 has excellent properties:
  - 2^32-th roots of unity (large NTT-friendly domain)
  - Fits in Python's native int (no bignum overhead)
  - Used by Plonky2, well-studied in production STARKs

All operations are mod p. Elements are plain Python ints.
"""

from __future__ import annotations

# Goldilocks prime: 2^64 - 2^32 + 1
P = 18446744069414584321

# Primitive root of the multiplicative group (generator)
GENERATOR = 7


def add(a: int, b: int) -> int:
    return (a + b) % P


def sub(a: int, b: int) -> int:
    return (a - b) % P


def mul(a: int, b: int) -> int:
    return (a * b) % P


def neg(a: int) -> int:
    return (-a) % P


def inv(a: int) -> int:
    """Modular inverse via Fermat's little theorem: a^(-1) = a^(p-2) mod p."""
    if a == 0:
        raise ZeroDivisionError("Cannot invert zero")
    return pow(a, P - 2, P)


def exp(base: int, exponent: int) -> int:
    return pow(base, exponent, P)


def div(a: int, b: int) -> int:
    return mul(a, inv(b))


# ---------------------------------------------------------------------------
# Roots of unity
# ---------------------------------------------------------------------------


def root_of_unity(n: int) -> int:
    """
    Return a primitive n-th root of unity in the Goldilocks field.

    n must be a power of 2 and n <= 2^32.
    """
    if n & (n - 1) != 0:
        raise ValueError(f"n must be a power of 2, got {n}")
    if n > (1 << 32):
        raise ValueError(f"n must be <= 2^32, got {n}")

    # omega = g^((p-1)/n) where g is the primitive root
    return exp(GENERATOR, (P - 1) // n)


# ---------------------------------------------------------------------------
# NTT (Number Theoretic Transform) — Cooley-Tukey radix-2
# ---------------------------------------------------------------------------


def ntt(coeffs: list[int], omega: int) -> list[int]:
    """
    Forward NTT: evaluate polynomial at powers of omega.

    Input:  coefficient representation [a_0, a_1, ..., a_{n-1}]
    Output: evaluation representation [p(1), p(omega), p(omega^2), ...]

    n must be a power of 2.
    """
    n = len(coeffs)
    if n == 1:
        return [coeffs[0] % P]

    vals = [c % P for c in coeffs]
    # Bit-reversal permutation
    log_n = n.bit_length() - 1
    for i in range(n):
        j = _bit_reverse(i, log_n)
        if i < j:
            vals[i], vals[j] = vals[j], vals[i]

    # Butterfly stages
    length = 2
    while length <= n:
        w = exp(omega, n // length)
        for start in range(0, n, length):
            wk = 1
            for k in range(length // 2):
                u = vals[start + k]
                v = mul(vals[start + k + length // 2], wk)
                vals[start + k] = add(u, v)
                vals[start + k + length // 2] = sub(u, v)
                wk = mul(wk, w)
        length *= 2

    return vals


def intt(values: list[int], omega: int) -> list[int]:
    """
    Inverse NTT: interpolate from evaluations back to coefficients.

    Uses NTT with omega^{-1} and scales by n^{-1}.
    """
    n = len(values)
    omega_inv = inv(omega)
    result = ntt(values, omega_inv)
    n_inv = inv(n)
    return [mul(r, n_inv) for r in result]


def _bit_reverse(x: int, bits: int) -> int:
    result = 0
    for _ in range(bits):
        result = (result << 1) | (x & 1)
        x >>= 1
    return result


# ---------------------------------------------------------------------------
# Polynomial utilities
# ---------------------------------------------------------------------------


def poly_eval(coeffs: list[int], point: int) -> int:
    """Evaluate polynomial at a point using Horner's method."""
    result = 0
    for c in reversed(coeffs):
        result = add(mul(result, point), c % P)
    return result


def poly_add(a: list[int], b: list[int]) -> list[int]:
    """Add two polynomials (coefficient-wise)."""
    n = max(len(a), len(b))
    result = [0] * n
    for i in range(len(a)):
        result[i] = add(result[i], a[i])
    for i in range(len(b)):
        result[i] = add(result[i], b[i])
    return result


def poly_mul_scalar(coeffs: list[int], scalar: int) -> list[int]:
    """Multiply polynomial by a scalar."""
    return [mul(c, scalar) for c in coeffs]


BYTES_PER_ELEMENT = 7  # 7 bytes = 56 bits, safely < Goldilocks prime


def bytes_to_field_elements(data: bytes) -> list[int]:
    """
    Encode raw bytes as Goldilocks field elements.

    Each 7-byte chunk becomes one field element (7 bytes < 2^56 < p).
    Last chunk is right-padded with zeros before encoding.
    """
    elements = []
    for i in range(0, len(data), BYTES_PER_ELEMENT):
        chunk = data[i : i + BYTES_PER_ELEMENT]
        # Right-pad short chunks so bytes decode correctly
        padded = chunk + b"\x00" * (BYTES_PER_ELEMENT - len(chunk))
        elements.append(int.from_bytes(padded, "big"))
    return elements


def field_elements_to_bytes(elements: list[int], total_len: int) -> bytes:
    """Decode Goldilocks field elements back to raw bytes."""
    parts = []
    for e in elements:
        parts.append(e.to_bytes(BYTES_PER_ELEMENT, "big"))
    return b"".join(parts)[:total_len]
