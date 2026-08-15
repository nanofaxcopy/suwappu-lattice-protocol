/-
Counting lemmas over `List.countP`.

These are the only "library" facts the corridor proofs need. They are
stated over an arbitrary list so the corridor theorems are parametric in
the roster and quorum sizes — the concrete 7-of-9 instantiation is just
one corollary among many.

Everything here is proved from Lean 4 core. No Mathlib.
-/

namespace Suwappu.Counting

universe u
variable {α : Type u}

/-- Signers counted under a conjunction, plus the roster size, dominates
the two individual counts. This is inclusion–exclusion in the only form
we need: `|A| + |B| ≤ |A ∩ B| + |U|`.

Proved by induction, one element at a time: an element contributes at
most 1 to each side's "extra", and whenever it contributes 2 on the left
(it satisfies both predicates) it also contributes 1 to the intersection
on the right. -/
theorem countP_add_le_inter_add_length (p q : α → Bool) :
    ∀ l : List α,
      l.countP p + l.countP q ≤ l.countP (fun a => p a && q a) + l.length
  | [] => by simp
  | a :: t => by
    have ih := countP_add_le_inter_add_length p q t
    simp only [List.countP_cons, List.length_cons]
    cases hp : p a <;> cases hq : q a <;> simp [hp, hq] <;> omega

/-- If `p` implies `q` pointwise, `p` counts no more than `q`. -/
theorem countP_mono (p q : α → Bool) (h : ∀ a, p a = true → q a = true) :
    ∀ l : List α, l.countP p ≤ l.countP q
  | [] => by simp
  | a :: t => by
    have ih := countP_mono p q h t
    simp only [List.countP_cons]
    cases hp : p a
    · cases hq : q a <;> simp [hp, hq] <;> omega
    · have : q a = true := h a hp
      simp [hp, this]
      omega

/-- A nonzero count yields a witness in the list satisfying the predicate. -/
theorem exists_of_countP_pos (p : α → Bool) :
    ∀ {l : List α}, 0 < l.countP p → ∃ a, a ∈ l ∧ p a = true
  | [], h => by simp at h
  | a :: t, h => by
    simp only [List.countP_cons] at h
    cases hp : p a
    · simp [hp] at h
      obtain ⟨b, hb, hpb⟩ := exists_of_countP_pos p h
      exact ⟨b, List.mem_cons_of_mem _ hb, hpb⟩
    · exact ⟨a, List.mem_cons_self _ _, hp⟩

/-- Counting is bounded by length. -/
theorem countP_le_length (p : α → Bool) :
    ∀ l : List α, l.countP p ≤ l.length
  | [] => by simp
  | a :: t => by
    have ih := countP_le_length p t
    simp only [List.countP_cons, List.length_cons]
    cases hp : p a <;> simp [hp] <;> omega

/-- Splitting a count along a second predicate. -/
theorem countP_split (p q : α → Bool) :
    ∀ l : List α,
      l.countP p
        = l.countP (fun a => p a && q a) + l.countP (fun a => p a && !q a)
  | [] => by simp
  | a :: t => by
    have ih := countP_split p q t
    simp only [List.countP_cons]
    cases hp : p a <;> cases hq : q a <;> simp [hp, hq] <;> omega

/-- A predicate and its negation partition the list. -/
theorem countP_add_countP_not (p : α → Bool) :
    ∀ l : List α, l.countP p + l.countP (fun a => !p a) = l.length
  | [] => by simp
  | a :: t => by
    have ih := countP_add_countP_not p t
    simp only [List.countP_cons, List.length_cons]
    cases hp : p a <;> simp [hp] <;> omega

end Suwappu.Counting
