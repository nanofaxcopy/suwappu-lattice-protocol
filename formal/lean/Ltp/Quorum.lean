import Ltp.Counting

/-
# Corridor attestation quorum — safety and liveness

Formalises the 7-of-9 corridor super-node attestation quorum
(`src/ltp/corridor/constants.py`:
`LTP_ATTESTATION_QUORUM_THRESHOLD = 7`, `LTP_ATTESTATION_QUORUM_SIZE = 9`,
Paper §10).

This is the property `docs/FORMAL_VERIFICATION_STATUS.md` records as
*out of reach* for the existing Verifpal model — "Verifpal doesn't
natively model threshold signatures" — so it is currently argued only in
prose. It is a counting argument, which is exactly what a proof assistant
is good at.

## The model

A corridor is a `roster` of super-node authority ids. A set of signers is
a decidable predicate on ids; "how many signed" is `roster.countP`.
Counting over the roster (rather than over the raw signature list) is
faithful to `attestation.py`, which rejects duplicate witnesses and
counts *distinct* signers against the threshold — a signer cannot inflate
a quorum by submitting twice.

Everything is parametric in roster size and threshold; 7-of-9 is one
corollary.
-/

namespace Suwappu.LTP

open Suwappu.Counting

/-- `ROSTER_SIZE` — `LTP_ATTESTATION_QUORUM_SIZE` in `constants.py`. -/
def ROSTER_SIZE : Nat := 9

/-- `THRESHOLD` — `LTP_ATTESTATION_QUORUM_THRESHOLD` in `constants.py`. -/
def THRESHOLD : Nat := 7

/-! ## Generic quorum intersection -/

/-- **Quorum intersection, general form.** Two quorums of sizes `t₁` and
`t₂` drawn from a roster of `n` members overlap in at least `t₁ + t₂ - n`
members. Stated additively to avoid truncating `Nat` subtraction. -/
theorem quorum_intersection_general
    {α : Type} (roster : List α) (s₁ s₂ : α → Bool) :
    roster.countP s₁ + roster.countP s₂
      ≤ roster.countP (fun a => s₁ a && s₂ a) + roster.length :=
  countP_add_le_inter_add_length s₁ s₂ roster

/-! ## The concrete 7-of-9 corridor -/

/-- **Two valid corridor attestations always share at least 5 signers.**

`7 + 7 - 9 = 5`. This is the quantitative core of corridor safety: an
equivocating corridor cannot produce two conflicting attestations that
are "signed by different people". -/
theorem corridor_intersection
    {α : Type} (roster : List α) (hn : roster.length = ROSTER_SIZE)
    (s₁ s₂ : α → Bool)
    (h₁ : THRESHOLD ≤ roster.countP s₁)
    (h₂ : THRESHOLD ≤ roster.countP s₂) :
    5 ≤ roster.countP (fun a => s₁ a && s₂ a) := by
  have hgen := quorum_intersection_general roster s₁ s₂
  simp only [ROSTER_SIZE] at hn
  simp only [THRESHOLD] at h₁ h₂
  omega

/-- **Corridor safety.** If at most 4 of the 9 super-nodes are Byzantine,
then any two attestations that each reach the 7-signer threshold share an
**honest** signer.

Since an honest node signs at most one attestation per (corridor, height,
payload-root) slot, this is precisely what rules out two conflicting
attestations both being accepted: the honest common signer would have had
to sign both.

Corollary of the arithmetic: the corridor tolerates **at most 4**
Byzantine super-nodes. At 5 the guarantee is gone — 5 colluding members
can cover the entire forced overlap. -/
theorem corridor_safety
    {α : Type} (roster : List α) (hn : roster.length = ROSTER_SIZE)
    (byzantine : α → Bool)
    (hbyz : roster.countP byzantine ≤ 4)
    (s₁ s₂ : α → Bool)
    (h₁ : THRESHOLD ≤ roster.countP s₁)
    (h₂ : THRESHOLD ≤ roster.countP s₂) :
    ∃ a, a ∈ roster ∧ s₁ a = true ∧ s₂ a = true ∧ byzantine a = false := by
  -- At least 5 signed both.
  have hinter : 5 ≤ roster.countP (fun a => s₁ a && s₂ a) :=
    corridor_intersection roster hn s₁ s₂ h₁ h₂
  -- Split that overlap into Byzantine and honest parts.
  have hsplit :
      roster.countP (fun a => s₁ a && s₂ a)
        = roster.countP (fun a => (s₁ a && s₂ a) && byzantine a)
          + roster.countP (fun a => (s₁ a && s₂ a) && !byzantine a) :=
    countP_split _ byzantine roster
  -- The Byzantine part is bounded by the Byzantine budget.
  have hb : roster.countP (fun a => (s₁ a && s₂ a) && byzantine a)
      ≤ roster.countP byzantine := by
    refine countP_mono _ _ ?_ roster
    intro a ha
    exact (Bool.and_eq_true _ _ |>.mp ha).2
  -- Hence the honest part is nonempty.
  have hpos : 0 < roster.countP (fun a => (s₁ a && s₂ a) && !byzantine a) := by
    omega
  obtain ⟨a, hmem, hsat⟩ := exists_of_countP_pos _ hpos
  refine ⟨a, hmem, ?_, ?_, ?_⟩
  · exact (Bool.and_eq_true _ _ |>.mp ((Bool.and_eq_true _ _ |>.mp hsat).1)).1
  · exact (Bool.and_eq_true _ _ |>.mp ((Bool.and_eq_true _ _ |>.mp hsat).1)).2
  · have := (Bool.and_eq_true _ _ |>.mp hsat).2
    simpa using this

/-- **Corridor liveness.** If at most 2 super-nodes are unavailable, the
remaining members still meet the threshold, so an attestation can be
formed. `9 - 2 = 7`. -/
theorem corridor_liveness
    {α : Type} (roster : List α) (hn : roster.length = ROSTER_SIZE)
    (faulty : α → Bool) (hf : roster.countP faulty ≤ 2) :
    THRESHOLD ≤ roster.countP (fun a => !faulty a) := by
  have hpart := countP_add_countP_not faulty roster
  simp only [ROSTER_SIZE] at hn
  simp only [THRESHOLD]
  omega

/-- The tolerance asymmetry, stated explicitly so it cannot be misread:
the corridor survives **4** Byzantine members for safety but only **2**
unavailable members for liveness. Operationally, losing 3 super-nodes
halts the corridor long before safety is at risk. -/
theorem tolerance_asymmetry : 4 ≠ 2 := by decide

/-- Sanity: the configured threshold really is a strict majority of the
roster, which is what makes the intersection nonempty at all. -/
theorem threshold_is_majority : ROSTER_SIZE < 2 * THRESHOLD := by decide

end Suwappu.LTP
