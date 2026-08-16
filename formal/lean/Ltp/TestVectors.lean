/-
# The §2.1.1 interoperability test vectors, machine-checked

The whitepaper publishes two Reed-Solomon test vectors and mandates that
implementations validate against them. Test vectors are exactly the kind
of artifact that silently rots — review round 001 found the original
vector listed systematic shards contradicting the non-systematic spec,
and the pre-0.2.0 "Complete Test Vector" carried no shard bytes at all.

This file recomputes both 0.2.0 vectors *inside the Lean kernel*, from a
from-scratch GF(2⁸) implementation (irreducible polynomial 0x11D, the
same field as `src/ltp/erasure.py`), and `decide`s byte equality. If
anyone edits a byte of either vector in the paper — or changes the
field polynomial, the evaluation points, or the framing — regenerating
these constants forces the discrepancy into a failing proof.

Scope note, in the spirit of the rest of this development: this checks
the *encoding* arithmetic of the published vectors. Decoding (Vandermonde
inversion) is exercised by `tests/test_erasure.py` and
`tests/test_production_assertions.py::test_default_backend_matches_whitepaper_vector`,
which pins the same bytes against the reference implementation — so the
paper, the Lean kernel, and the Python implementation are three
independent computations agreeing on the same constants.
-/

namespace Suwappu.LTP.TestVectors

/-- Multiply by x in GF(2⁸) mod x⁸ + x⁴ + x³ + x² + 1 (0x11D). -/
def xtime (a : Nat) : Nat :=
  let b := a * 2
  if b < 256 then b else b ^^^ 0x11D

/-- Russian-peasant GF(2⁸) multiplication, 8 bits of `b`. -/
def gfMulAux : Nat → Nat → Nat → Nat → Nat
  | _, _, acc, 0 => acc
  | a, b, acc, fuel + 1 =>
      gfMulAux (xtime a) (b / 2) (if b % 2 = 1 then acc ^^^ a else acc) fuel

/-- GF(2⁸) multiplication under 0x11D. -/
def gfMul (a b : Nat) : Nat := gfMulAux a b 0 8

/-- Sanity: the generator relation the field is built on, and the two
products the paper's worked arithmetic uses. -/
theorem gf_sanity :
    gfMul 2 3 = 6 ∧ gfMul 2 4 = 8 ∧ gfMul 3 3 = 5 ∧ gfMul 4 4 = 16
      ∧ gfMul 0x80 2 = 0x1D := by decide

/-- Bytewise XOR of two equal-length byte lists. -/
def bxor (xs ys : List Nat) : List Nat := List.zipWith (· ^^^ ·) xs ys

/-- Evaluate the shard at point `α` for `k = 2`: `c₀ ⊕ α·c₁`. -/
def shard2 (α : Nat) (c₀ c₁ : List Nat) : List Nat :=
  bxor c₀ (c₁.map (gfMul α))

/-- Evaluate the shard at point `α` for `k = 3`: `d₀ ⊕ α·d₁ ⊕ α²·d₂`. -/
def shard3 (α : Nat) (d₀ d₁ d₂ : List Nat) : List Nat :=
  bxor (bxor d₀ (d₁.map (gfMul α))) (d₂.map (gfMul (gfMul α α)))

/-! ## Vector 1 — bare matrix encoding (§2.1.1)

Chunks c₀ = [0x01, 0x02], c₁ = [0x03, 0x04], evaluation points 1..4,
framing omitted (as the paper states). -/

theorem vector1_matches :
    [shard2 1 [0x01, 0x02] [0x03, 0x04],
     shard2 2 [0x01, 0x02] [0x03, 0x04],
     shard2 3 [0x01, 0x02] [0x03, 0x04],
     shard2 4 [0x01, 0x02] [0x03, 0x04]]
      = [[0x02, 0x06], [0x07, 0x0A], [0x04, 0x0E], [0x0D, 0x12]] := by
  decide

/-- The non-systematic property the paper calls out on this vector:
no shard equals a raw data chunk. -/
theorem vector1_non_systematic :
    shard2 1 [0x01, 0x02] [0x03, 0x04] ≠ [0x01, 0x02]
      ∧ shard2 1 [0x01, 0x02] [0x03, 0x04] ≠ [0x03, 0x04] := by decide

/-! ## Vector 2 — the Complete Test Vector ("Hello!", k = 3, n = 6)

Framing per §2.1.1: 8-byte big-endian length prefix (length 6), zero-pad
to 15 bytes, split into three 5-byte chunks. The chunks below are the
paper's Step 2 output; the equality checks the paper's Step 3 shard
bytes. -/

def d₀ : List Nat := [0x00, 0x00, 0x00, 0x00, 0x00]
def d₁ : List Nat := [0x00, 0x00, 0x06, 0x48, 0x65]
def d₂ : List Nat := [0x6C, 0x6C, 0x6F, 0x21, 0x00]

/-- The framing itself: prefix ‖ "Hello!" ‖ pad really does regroup into
d₀, d₁, d₂. (0x48 0x65 0x6C 0x6C 0x6F 0x21 = "Hello!", length 6.) -/
theorem vector2_framing :
    d₀ ++ d₁ ++ d₂
      = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x06,
         0x48, 0x65, 0x6C, 0x6C, 0x6F, 0x21, 0x00] := by decide

theorem vector2_matches :
    [shard3 1 d₀ d₁ d₂, shard3 2 d₀ d₁ d₂, shard3 3 d₀ d₁ d₂,
     shard3 4 d₀ d₁ d₂, shard3 5 d₀ d₁ d₂, shard3 6 d₀ d₁ d₂]
      = [[0x6C, 0x6C, 0x69, 0x69, 0x65],
         [0xAD, 0xAD, 0xAD, 0x14, 0xCA],
         [0xC1, 0xC1, 0xC4, 0x7D, 0xAF],
         [0x8E, 0x8E, 0xA6, 0x17, 0x89],
         [0xE2, 0xE2, 0xCF, 0x7E, 0xEC],
         [0x23, 0x23, 0x0B, 0x03, 0x43]] := by decide

end Suwappu.LTP.TestVectors
