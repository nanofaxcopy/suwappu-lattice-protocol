/-
# Constant-size on-chain commitment (Paper §10.2)

Formalises the load-bearing invariant recorded in
`suwappu-dag/CLAUDE.md` as #3:

> every LTP attestation commits ≈1,600 B regardless of payload …
> Changes that add per-payload bytes to the on-chain commitment surface
> are rejected.

Two separate claims live inside that sentence, and they have very
different truth status. This file proves the first and pins the second.

1. **Payload independence** — the commitment's size does not depend on
   the payload at all. This is the invariant that actually matters (it is
   what stops the on-chain footprint growing with transfer size), and it
   is *true*. `commitment_size_payload_independent` proves it.

2. **The figure "≈1,600 B"** — this matches no actual field layout.
   `src/ltp/corridor/envelope.py` already says so in prose; here it is
   machine-checked. With ML-KEM-768 the envelope totals 1,216 B; with
   ML-KEM-1024, 1,696 B. Neither is 1,600. The pinned constant
   `ON_CHAIN_COMMITMENT_BYTES = 1_600` is a paper-level approximation, and
   `OnChainCommitment.assert_strict_total()` is consequently unsatisfiable
   for every well-formed envelope — it is a documented forward-compat stub,
   not a live check.

Pinning (2) in Lean means that if anyone later "tidies" a field size to
make the arithmetic appear to work, the proof breaks and forces the
question to be answered deliberately rather than silently.
-/

namespace Suwappu.LTP.Commitment

/-! ## Field widths (bytes)

Sourced from `src/ltp/primitives.py` (`_REAL_KEM_CT = 1088`,
`_REAL_KEM5_CT = 1568`) and `src/ltp/corridor/envelope.py`. Note that the
docstring in `src/ltp/corridor/constants.py` labels 1,568 B as the
"ML-KEM-768 ciphertext"; it is not — 1,568 B is ML-KEM-**1024**.
ML-KEM-768's ciphertext is 1,088 B. -/

/-- Compressed G2 BLS12-381 aggregate signature. -/
def BLS_G2_COMPRESSED_BYTES : Nat := 96

/-- SHA3-256 payload root. -/
def SHA3_256_BYTES : Nat := 32

/-- ML-KEM-768 ciphertext (FIPS 203, Level 3). -/
def ML_KEM_768_CT_BYTES : Nat := 1088

/-- ML-KEM-1024 ciphertext (FIPS 203, Level 5). -/
def ML_KEM_1024_CT_BYTES : Nat := 1568

/-- The constant pinned in `constants.py` / the Rust crate. -/
def ON_CHAIN_COMMITMENT_BYTES : Nat := 1600

/-- Which ML-KEM parameter set seals the session key. -/
inductive KemParams where
  | mlKem768
  | mlKem1024
  deriving DecidableEq, Repr

/-- Ciphertext width for a parameter set. -/
def KemParams.ctBytes : KemParams → Nat
  | .mlKem768 => ML_KEM_768_CT_BYTES
  | .mlKem1024 => ML_KEM_1024_CT_BYTES

/-- The three-part envelope. The payload itself is **not** a field — it
lives off-chain and is committed to only through its 32-byte root. That
is the whole mechanism behind payload independence. -/
structure OnChainCommitment where
  kem : KemParams
  /-- The off-chain payload this commitment is *about*. Carried here only
  so we can state theorems quantified over it; it contributes no bytes. -/
  payload : List UInt8

/-- Serialized size: `sealed_session_key || aggregate_signature || payload_root`. -/
def OnChainCommitment.sizeBytes (c : OnChainCommitment) : Nat :=
  c.kem.ctBytes + BLS_G2_COMPRESSED_BYTES + SHA3_256_BYTES

/-! ## The invariant that matters -/

/-- **Payload independence.** Two commitments over the same parameter set
have the same size, whatever their payloads — including payloads of wildly
different lengths. This is Invariant 3's operative content. -/
theorem commitment_size_payload_independent
    (kem : KemParams) (p q : List UInt8) :
    OnChainCommitment.sizeBytes ⟨kem, p⟩ = OnChainCommitment.sizeBytes ⟨kem, q⟩ :=
  rfl

/-- Sharper form: the size is a function of the parameter set alone, so it
is literally constant in the payload. -/
theorem commitment_size_eq_const (c : OnChainCommitment) :
    c.sizeBytes = c.kem.ctBytes + 128 := by
  cases c with
  | mk kem payload =>
    cases kem <;> rfl

/-- No payload, however large, changes the on-chain footprint. Stated as
the contrapositive of "adds per-payload bytes". -/
theorem no_per_payload_bytes
    (kem : KemParams) (p : List UInt8) :
    OnChainCommitment.sizeBytes ⟨kem, p⟩ = OnChainCommitment.sizeBytes ⟨kem, []⟩ :=
  rfl

/-! ## The arithmetic that does not add up -/

/-- ML-KEM-768 envelope totals 1,216 B. -/
theorem total_768 : OnChainCommitment.sizeBytes ⟨.mlKem768, []⟩ = 1216 := by decide

/-- ML-KEM-1024 envelope totals 1,696 B. -/
theorem total_1024 : OnChainCommitment.sizeBytes ⟨.mlKem1024, []⟩ = 1696 := by decide

/-- **Neither parameter set hits the pinned 1,600 B constant.** This is
the prose caveat in `envelope.py` turned into a checked fact. -/
theorem strict_total_unsatisfiable (c : OnChainCommitment) :
    c.sizeBytes ≠ ON_CHAIN_COMMITMENT_BYTES := by
  cases c with
  | mk kem payload =>
    -- `decide` cannot fire while `payload` is free, even though the size
    -- does not depend on it; unfold to closed numerals first.
    cases kem <;>
      simp only [OnChainCommitment.sizeBytes, KemParams.ctBytes,
        ML_KEM_768_CT_BYTES, ML_KEM_1024_CT_BYTES,
        BLS_G2_COMPRESSED_BYTES, SHA3_256_BYTES,
        ON_CHAIN_COMMITMENT_BYTES] <;>
      decide

/-- Where the 1,600 figure evidently came from: it is exactly the
ML-KEM-1024 ciphertext plus the SHA3 root, i.e. the aggregate signature
was dropped from the sum, *and* that ciphertext was mislabeled as
ML-KEM-768. Both slips are needed to land on 1,600. -/
theorem provenance_of_1600 :
    ML_KEM_1024_CT_BYTES + SHA3_256_BYTES = ON_CHAIN_COMMITMENT_BYTES := by decide

/-- Corollary: the difference between the pinned constant and the real
ML-KEM-1024 envelope is exactly one aggregate signature. -/
theorem gap_is_one_signature :
    OnChainCommitment.sizeBytes ⟨.mlKem1024, []⟩
      = ON_CHAIN_COMMITMENT_BYTES + BLS_G2_COMPRESSED_BYTES := by decide

end Suwappu.LTP.Commitment
