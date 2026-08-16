import Ltp.Commitment

/-
# Constant-size sealed lattice key (Paper §2.2, §3.3.7)

Formalises the whitepaper's central bandwidth claim:

> **Sender→receiver decoupling**: Transferring 1 KB and transferring 1 TB
> produce the same size sealed lattice key (~1,300 bytes). The
> sender→receiver direct transmission is O(1).

and §3.3.7's row "*O(1) key size is proven*" — which until this file was
proven only for the corridor's on-chain commitment (`Ltp/Commitment.lean`),
not for the lattice key itself.

Also pinned here: the commitment record's size. The record carries an
ML-DSA-65 signature, which FIPS 204 fixes at 3,309 bytes — so the record
cannot be smaller than 3,309 bytes, and any prose claiming "< 1 KB" is
arithmetically impossible. (The 0.2.0 whitepaper revision states ≈3.5 KB.)

As everywhere in this development: these are proofs about the *sizes
declared in the specification*, not about serialized bytes produced by
`src/ltp/lattice.py`. The binding is by review.
-/

namespace Suwappu.LTP.LatticeKey

open Suwappu.LTP.Commitment (KemParams ML_KEM_768_CT_BYTES ML_KEM_1024_CT_BYTES)

/-! ## Field widths (bytes)

Inner-payload fields from Paper §2.2.1 ("The Lattice Key"): EntityID,
CEK, and commitment-record reference are 32-byte hashes/keys; the access
policy is a small variable-length JSON object (§2.2.1 bounds it to
roughly 20–50 bytes in the minimal encoding). The sealed form adds the
ML-KEM ciphertext and the AEAD tag. -/

/-- BLAKE3-256 EntityID. -/
def ENTITY_ID_BYTES : Nat := 32

/-- Content encryption key (XChaCha20-Poly1305 key). -/
def CEK_BYTES : Nat := 32

/-- Commitment-record reference (hash). -/
def COMMITMENT_REF_BYTES : Nat := 32

/-- Poly1305 AEAD tag. -/
def AEAD_TAG_BYTES : Nat := 16

/-- ML-DSA-65 signature size, fixed by FIPS 204. -/
def ML_DSA_65_SIG_BYTES : Nat := 3309

/-- A sealed lattice key. The entity payload is **not** a field — the key
carries only fixed-width references to it (hashes and the CEK). It is
carried here, as in `OnChainCommitment`, purely so theorems can quantify
over it; it contributes no bytes. -/
structure SealedLatticeKey where
  kem : KemParams
  /-- Variable-length access-policy encoding, in bytes. -/
  policyBytes : Nat
  /-- The off-chain entity this key unlocks. Contributes no bytes. -/
  payload : List UInt8

/-- Inner payload: `entity_id ‖ cek ‖ commitment_ref ‖ policy`. -/
def SealedLatticeKey.innerBytes (k : SealedLatticeKey) : Nat :=
  ENTITY_ID_BYTES + CEK_BYTES + COMMITMENT_REF_BYTES + k.policyBytes

/-- Sealed size: `kem_ciphertext ‖ AEAD(inner)` where the AEAD adds one tag. -/
def SealedLatticeKey.sizeBytes (k : SealedLatticeKey) : Nat :=
  k.kem.ctBytes + AEAD_TAG_BYTES + k.innerBytes

/-! ## The O(1) claim -/

/-- **Payload independence.** Two sealed lattice keys with the same
parameter set and policy have the same size whatever entities they
unlock — 1 KB or 1 TB. This is the whitepaper's "sender→receiver
decoupling" claim in machine-checked form. -/
theorem lattice_key_size_payload_independent
    (kem : KemParams) (policy : Nat) (p q : List UInt8) :
    SealedLatticeKey.sizeBytes ⟨kem, policy, p⟩
      = SealedLatticeKey.sizeBytes ⟨kem, policy, q⟩ :=
  rfl

/-- Sharper form: the size is a function of the parameter set and the
policy width alone. -/
theorem lattice_key_size_eq_const (k : SealedLatticeKey) :
    k.sizeBytes = k.kem.ctBytes + AEAD_TAG_BYTES + 96 + k.policyBytes := by
  cases k with
  | mk kem policy payload =>
    simp [SealedLatticeKey.sizeBytes, SealedLatticeKey.innerBytes,
      ENTITY_ID_BYTES, CEK_BYTES, COMMITMENT_REF_BYTES]
    omega

/-! ## Pinning the "~1,300 bytes" figure

With ML-KEM-768 and the §2.2.1 policy bound (20–50 bytes), the sealed
key is 1,220–1,250 bytes — the paper's "~1,300 bytes" is a round-up, not
an exact layout. The inner payload is 116–146 bytes (the 0.2.0 revision
says "~120–150"; the pre-0.2.0 "~160 bytes" matched no field sum). -/

/-- Lower bound at ML-KEM-768 with the minimal 20-byte policy. -/
theorem sealed_768_min :
    SealedLatticeKey.sizeBytes ⟨.mlKem768, 20, []⟩ = 1220 := by decide

/-- Upper bound at ML-KEM-768 with the maximal 50-byte policy. -/
theorem sealed_768_max :
    SealedLatticeKey.sizeBytes ⟨.mlKem768, 50, []⟩ = 1250 := by decide

/-- The sealed key stays within the paper's advertised ~1,300-byte
envelope for every policy up to 96 bytes — twice the specified maximum. -/
theorem sealed_768_bounded (policy : Nat) (p : List UInt8)
    (hp : policy ≤ 96) :
    SealedLatticeKey.sizeBytes ⟨.mlKem768, policy, p⟩ ≤ 1300 := by
  simp [SealedLatticeKey.sizeBytes, SealedLatticeKey.innerBytes,
    KemParams.ctBytes, ML_KEM_768_CT_BYTES, AEAD_TAG_BYTES,
    ENTITY_ID_BYTES, CEK_BYTES, COMMITMENT_REF_BYTES]
  omega

/-- Inner-payload bounds for the specified 20–50-byte policy range:
116–146 bytes. -/
theorem inner_payload_bounds (policy : Nat) (p : List UInt8) (kem : KemParams)
    (h₁ : 20 ≤ policy) (h₂ : policy ≤ 50) :
    116 ≤ SealedLatticeKey.innerBytes ⟨kem, policy, p⟩
      ∧ SealedLatticeKey.innerBytes ⟨kem, policy, p⟩ ≤ 146 := by
  simp [SealedLatticeKey.innerBytes,
    ENTITY_ID_BYTES, CEK_BYTES, COMMITMENT_REF_BYTES]
  omega

/-! ## The commitment record is not "< 1 KB" -/

/-- Any record carrying an ML-DSA-65 signature is at least 3,309 bytes,
whatever its other fields total — more than three times the "< 1 KB" the
pre-0.2.0 whitepaper claimed. The signature width is fixed by FIPS 204;
this is the same class of fact as `strict_total_unsatisfiable`: a prose
size claim turned into checked arithmetic so it cannot silently
regress. -/
theorem record_exceeds_1kb (otherFieldBytes : Nat) :
    1024 < ML_DSA_65_SIG_BYTES + otherFieldBytes := by
  simp [ML_DSA_65_SIG_BYTES]
  omega

end Suwappu.LTP.LatticeKey
