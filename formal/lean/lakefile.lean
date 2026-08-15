import Lake
open Lake DSL

/-
Machine-checked proofs for the LTP corridor bridge.

Deliberately depends on NOTHING but Lean 4 core — no Mathlib. The
counting lemmas we need are short enough to prove from scratch, and a
Mathlib dependency would add a multi-gigabyte download to this repo's CI
for no proof-strength gain. `lake build` runs in seconds.
-/
package ltp where
  leanOptions := #[⟨`autoImplicit, false⟩]

@[default_target]
lean_lib Ltp where
  roots := #[`Ltp]
