import Ltp.Counting

/-
# Governance supermajority — safety and liveness (Paper §5.1)

The whitepaper's governance and trust-tier claims:

> Transition between stages … requires a supermajority (≥ 2/3) of
> existing operators to approve via signed votes.

> | BFT replicated log (PBFT/Raft) | f < n/3 Byzantine operators |
> | BFT consensus | > 2/3 honest operators |

These are the classical BFT bounds, and — like the corridor's 7-of-9
quorum in `Ltp/Quorum.lean` — they are counting arguments the paper
states in prose. The corridor theorems are the fixed-size instance
(7-of-9); these are the fractional-threshold general case: any two
2/3-supermajorities overlap in an honest voter when fewer than n/3
voters are Byzantine, and a supermajority is still formable when fewer
than n/3 voters are down.

Thresholds are stated multiplicatively (`2·n ≤ 3·votes`, `3·byz < n`)
to avoid `Nat` division; this is the exact "≥ 2/3" / "< n/3" content
without rounding ambiguity.
-/

namespace Suwappu.LTP.Governance

open Suwappu.Counting

variable {α : Type}

/-- **Supermajority intersection is honest.** With fewer than n/3
Byzantine operators, any two ≥ 2/3 supermajorities share at least one
honest voter. This is what makes conflicting governance decisions
(both claiming ratification) impossible without an honest equivocator —
the same argument shape as `corridor_safety`, at the classical BFT
fractions. -/
theorem supermajority_safety
    (roster : List α) (byzantine : α → Bool)
    (hbyz : 3 * roster.countP byzantine < roster.length)
    (s₁ s₂ : α → Bool)
    (h₁ : 2 * roster.length ≤ 3 * roster.countP s₁)
    (h₂ : 2 * roster.length ≤ 3 * roster.countP s₂) :
    ∃ a, a ∈ roster ∧ s₁ a = true ∧ s₂ a = true ∧ byzantine a = false := by
  -- Two supermajorities force a large overlap …
  have hgen := countP_add_le_inter_add_length s₁ s₂ roster
  -- … split it into Byzantine and honest parts …
  have hsplit :
      roster.countP (fun a => s₁ a && s₂ a)
        = roster.countP (fun a => (s₁ a && s₂ a) && byzantine a)
          + roster.countP (fun a => (s₁ a && s₂ a) && !byzantine a) :=
    countP_split _ byzantine roster
  -- … the Byzantine part cannot exceed the Byzantine budget …
  have hb : roster.countP (fun a => (s₁ a && s₂ a) && byzantine a)
      ≤ roster.countP byzantine := by
    refine countP_mono _ _ ?_ roster
    intro a ha
    exact (Bool.and_eq_true _ _ |>.mp ha).2
  -- … so the honest part is nonempty:
  -- 3·overlap ≥ 3(c₁+c₂) − 3n ≥ 4n − 3n = n > 3·byz.
  have hpos : 0 < roster.countP (fun a => (s₁ a && s₂ a) && !byzantine a) := by
    omega
  obtain ⟨a, hmem, hsat⟩ := exists_of_countP_pos _ hpos
  refine ⟨a, hmem, ?_, ?_, ?_⟩
  · exact (Bool.and_eq_true _ _ |>.mp ((Bool.and_eq_true _ _ |>.mp hsat).1)).1
  · exact (Bool.and_eq_true _ _ |>.mp ((Bool.and_eq_true _ _ |>.mp hsat).1)).2
  · have := (Bool.and_eq_true _ _ |>.mp hsat).2
    simpa using this

/-- **Supermajority liveness.** With fewer than n/3 operators
unavailable, the remaining operators still form a strict 2/3
supermajority, so governance can proceed. (`3·(n − f) ≥ 2n + (n − 3f)
> 2n`.) -/
theorem supermajority_liveness
    (roster : List α) (faulty : α → Bool)
    (hf : 3 * roster.countP faulty < roster.length) :
    2 * roster.length < 3 * roster.countP (fun a => !faulty a) := by
  have hpart := countP_add_countP_not faulty roster
  omega

/-- The classical bound is tight in the safety direction: at exactly
n/3 Byzantine voters (n = 3f), two supermajorities can overlap entirely
inside the Byzantine set. Witness: n = 3, one Byzantine voter, both
"supermajorities" of size 2 containing it — the honest overlap can be
empty. Recorded as a concrete counterexample so nobody "improves" the
hypothesis to `≤`. -/
theorem safety_bound_tight :
    ∃ (roster : List Nat) (byzantine s₁ s₂ : Nat → Bool),
      3 * roster.countP byzantine = roster.length
        ∧ 2 * roster.length ≤ 3 * roster.countP s₁
        ∧ 2 * roster.length ≤ 3 * roster.countP s₂
        ∧ ¬ ∃ a, a ∈ roster ∧ s₁ a = true ∧ s₂ a = true
            ∧ byzantine a = false := by
  refine ⟨[0, 1, 2], fun a => decide (a = 0),
    fun a => decide (a ≤ 1), fun a => decide (a = 0 ∨ a = 2), ?_, ?_, ?_, ?_⟩
  · decide
  · decide
  · decide
  · intro ⟨a, hmem, h₁, h₂, h₃⟩
    simp at hmem
    rcases hmem with rfl | rfl | rfl <;> simp_all

end Suwappu.LTP.Governance
