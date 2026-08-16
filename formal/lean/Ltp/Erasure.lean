import Ltp.Counting

/-
# Erasure-coding reconstruction threshold (Paper §2.1.1, §4.3, §5)

The whitepaper's sharpest operational claims about the k-of-n code:

> Let A_i denote the event that shard index i has at least one available
> replica. The entity is reconstructable if and only if |{i : A_i}| ≥ k.

> - The first k data shards are NOT privileged — any k shards suffice
> - Losing ALL data shards (indices 0 through k−1) is survivable if k
>   parity shards remain
> - The failure boundary is sharp: at k shards the entity reconstructs
>   exactly; at k−1 it is irrecoverable

## What is assumed, what is derived

The MDS property itself — any k rows of the n×k Vandermonde matrix over
GF(2⁸) are invertible — is **assumed**, not proved here. Proving it needs
finite-field linear algebra (a Mathlib-sized dependency this development
deliberately excludes); it is instead evidenced by `tests/test_erasure.py`
and the independent recomputation in external math review 002. The
assumption enters as a single hypothesis, `hmds`, of the exact shape the
paper states: *decodability depends only on how many shards are
available, with threshold k*.

Everything the paper *derives from* that property is proved below. The
point of the exercise: the three bullets above are consequences of the
threshold shape alone, and formalising them shows nothing else is being
smuggled in — in particular, "the first k shards are not privileged" is
literally the statement that decodability is a function of the
availability *count*, which is what `hmds` says.

Shards are a `List α` of shard identifiers (the house style —
cf. `Ltp/Quorum.lean`); an availability predicate `avail : α → Bool`
plays the role of {i : A_i}.
-/

namespace Suwappu.LTP.Erasure

open Suwappu.Counting

variable {α : Type}

/-- **Reconstructable iff ≥ k shards available.** The paper's §4.3 claim,
restated from the MDS hypothesis — recorded so the interface the other
theorems consume is itself a named, audited fact. -/
theorem reconstructable_iff
    (shards : List α) (k : Nat) (dec : (α → Bool) → Prop)
    (hmds : ∀ s, dec s ↔ k ≤ shards.countP s)
    (avail : α → Bool) :
    dec avail ↔ k ≤ shards.countP avail :=
  hmds avail

/-- **No shard index is privileged.** Any two availability patterns with
the same count are equi-decodable — which k shards you hold is
irrelevant. "Losing all data shards is survivable if k parity shards
remain" is the instance where `s₁` holds only parity indices. -/
theorem no_index_privileged
    (shards : List α) (k : Nat) (dec : (α → Bool) → Prop)
    (hmds : ∀ s, dec s ↔ k ≤ shards.countP s)
    (s₁ s₂ : α → Bool)
    (hcount : shards.countP s₁ = shards.countP s₂) :
    dec s₁ ↔ dec s₂ := by
  rw [hmds s₁, hmds s₂, hcount]

/-- **The failure boundary is sharp, upper half:** k available shards
reconstruct. -/
theorem at_threshold_decodable
    (shards : List α) (k : Nat) (dec : (α → Bool) → Prop)
    (hmds : ∀ s, dec s ↔ k ≤ shards.countP s)
    (avail : α → Bool) (h : shards.countP avail = k) :
    dec avail := by
  rw [hmds]
  omega

/-- **The failure boundary is sharp, lower half:** k−1 (or fewer)
available shards are irrecoverable. This is also §3.3.5's threshold-
secrecy interface: below k, reconstruction is impossible *in this model*
(the cryptographic non-leakage of partial shards is a separate,
computational claim — see the paper's §3.3.5 note). -/
theorem below_threshold_undecodable
    (shards : List α) (k : Nat) (dec : (α → Bool) → Prop)
    (hmds : ∀ s, dec s ↔ k ≤ shards.countP s)
    (avail : α → Bool) (h : shards.countP avail < k) :
    ¬ dec avail := by
  rw [hmds]
  omega

/-- Losing shards never helps: decodability is monotone in availability.
(If a pattern decodes, any pointwise-larger pattern decodes.) -/
theorem decodable_monotone
    (shards : List α) (k : Nat) (dec : (α → Bool) → Prop)
    (hmds : ∀ s, dec s ↔ k ≤ shards.countP s)
    (s₁ s₂ : α → Bool) (hsub : ∀ a, s₁ a = true → s₂ a = true)
    (h₁ : dec s₁) : dec s₂ := by
  rw [hmds] at h₁ ⊢
  exact Nat.le_trans h₁ (countP_mono s₁ s₂ hsub shards)

/-- Durability headroom: with n shards of which at most `n − k` are
unavailable, the entity reconstructs. This is the availability form
operators actually monitor (§5: "loss budget"). -/
theorem loss_budget
    (shards : List α) (k : Nat) (dec : (α → Bool) → Prop)
    (hmds : ∀ s, dec s ↔ k ≤ shards.countP s)
    (lost : α → Bool)
    (hlost : shards.countP lost + k ≤ shards.length) :
    dec (fun a => !lost a) := by
  rw [hmds]
  have hpart := countP_add_countP_not lost shards
  omega

end Suwappu.LTP.Erasure
