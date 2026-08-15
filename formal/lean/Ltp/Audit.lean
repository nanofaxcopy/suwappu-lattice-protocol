import Ltp.Counting
import Ltp.Quorum
import Ltp.Commitment

/-
Axiom audit.

A Lean file that compiles proves nothing on its own — `sorry` also
compiles (with a warning that is easy to lose in CI output). This module
prints the axiom dependencies of every headline theorem. The expected
output for each is at most Lean's three standard axioms:

    [propext, Classical.choice, Quot.sound]

If `sorryAx` ever appears in this list, a proof has a hole. `verify.sh`
greps for exactly that and fails the build.
-/

open Suwappu.Counting
open Suwappu.LTP
open Suwappu.LTP.Commitment

-- Counting foundations
#print axioms Suwappu.Counting.countP_add_le_inter_add_length
#print axioms Suwappu.Counting.countP_mono
#print axioms Suwappu.Counting.exists_of_countP_pos
#print axioms Suwappu.Counting.countP_le_length
#print axioms Suwappu.Counting.countP_split
#print axioms Suwappu.Counting.countP_add_countP_not

-- Corridor quorum
#print axioms Suwappu.LTP.quorum_intersection_general
#print axioms Suwappu.LTP.corridor_intersection
#print axioms Suwappu.LTP.corridor_safety
#print axioms Suwappu.LTP.corridor_liveness
#print axioms Suwappu.LTP.threshold_is_majority

-- Commitment size
#print axioms Suwappu.LTP.Commitment.commitment_size_payload_independent
#print axioms Suwappu.LTP.Commitment.commitment_size_eq_const
#print axioms Suwappu.LTP.Commitment.no_per_payload_bytes
#print axioms Suwappu.LTP.Commitment.total_768
#print axioms Suwappu.LTP.Commitment.total_1024
#print axioms Suwappu.LTP.Commitment.strict_total_unsatisfiable
#print axioms Suwappu.LTP.Commitment.provenance_of_1600
#print axioms Suwappu.LTP.Commitment.gap_is_one_signature
