# LTP Lean proofs

Machine-checked proofs about the LTP corridor bridge, in Lean 4.

```bash
formal/lean/verify.sh        # build + hole scan + axiom audit
scripts/verify.sh formal     # same, via the repo's lane runner
```

No Mathlib. The development depends on Lean 4 core only — the counting
lemmas it needs are ~40 lines, and a Mathlib dependency would put a
multi-gigabyte download in front of every CI run for no gain in proof
strength. A cold `lake build` takes seconds.

## What is proved

| Theorem | File | Claim |
|---|---|---|
| `corridor_intersection` | `Ltp/Quorum.lean` | Any two 7-of-9 attestations share ≥ 5 signers |
| `corridor_safety` | `Ltp/Quorum.lean` | With ≤ 4 Byzantine super-nodes, any two attestations share an **honest** signer — so two conflicting attestations cannot both be valid |
| `corridor_liveness` | `Ltp/Quorum.lean` | With ≤ 2 unavailable super-nodes, a quorum is still formable |
| `commitment_size_payload_independent` | `Ltp/Commitment.lean` | The on-chain commitment size does not depend on the payload (Paper §10.2 / DAG Invariant 3) |
| `strict_total_unsatisfiable` | `Ltp/Commitment.lean` | No valid envelope totals the pinned 1,600 B — see below |

Plus the generic forms (`quorum_intersection_general`) so the results are
not specific to 7-of-9, and the counting lemmas they rest on.

### Why this, and not Verifpal

[`docs/FORMAL_VERIFICATION_STATUS.md`](../../docs/FORMAL_VERIFICATION_STATUS.md)
records the corridor threshold quorum as **out of reach** for the existing
symbolic model — *"Verifpal doesn't natively model threshold
signatures"* — and lists a Tamarin/ProVerif model of the 7-of-9 flow as
wanted work. The quorum guarantee is a counting argument, which is what a
proof assistant is for. These proofs cover that specific gap; they do not
replace the symbolic model, which covers confidentiality and
authentication properties Lean says nothing about here.

### The 1,600 B discrepancy

`ON_CHAIN_COMMITMENT_BYTES = 1_600` matches no actual field layout:

- ML-KEM-768: `1088 + 96 + 32 = 1216`
- ML-KEM-1024: `1568 + 96 + 32 = 1696`

`src/ltp/corridor/envelope.py` already says this in prose and marks
`assert_strict_total()` as a forward-compatibility stub. The proofs pin it
as a checked fact (`strict_total_unsatisfiable`), along with where 1,600
appears to have come from (`provenance_of_1600`: it is the ML-KEM-**1024**
ciphertext plus the SHA3 root, with the aggregate signature dropped and
the ciphertext mislabeled as ML-KEM-768).

The invariant that actually matters — payload independence — is true and
proved. Only the specific number is wrong.

## What is NOT proved — read this before citing these results

**These are proofs about a model, not about the implementation.** The
model is a few dozen lines of Lean; `src/ltp/` is thousands of lines of
Python and Solidity. Nothing here is extracted to, or mechanically linked
with, the running code. Specifically out of scope:

- **That the Python matches the model.** `attestation.py` counting
  distinct signers, rejecting duplicate witnesses, and verifying PoPs is
  asserted by the model's shape, not verified against the code. The
  binding is by review and by the CI trigger on `constants.py`.
- **Cryptographic soundness.** BLS aggregate-signature unforgeability,
  ML-KEM IND-CCA2, ML-DSA EUF-CMA — all assumed. A signer "signing" is an
  uninterpreted predicate. Rogue-key resistance comes from the PoP
  requirement (LTP-A-015), which is a protocol-level mitigation this model
  does not analyse.
- **That an honest node signs at most one attestation per slot.** This is
  an *assumption* of `corridor_safety`, discharged operationally (an
  honest implementation does not equivocate), not proved.
- **Liveness under partition, timing, or DoS.** No temporal or network
  model here at all.
- **Everything the symbolic model covers** — confidentiality,
  authentication, the Dolev-Yao attacker. Different tool, different file.

The honest summary: this converts the corridor's quorum arithmetic and
commitment-size invariant from prose claims into machine-checked ones. It
does not verify the bridge.

## Layout

```
formal/lean/
  lakefile.lean        package (no dependencies)
  lean-toolchain       pinned Lean version
  Ltp.lean             root import
  Ltp/Counting.lean    countP lemmas, proved from core
  Ltp/Quorum.lean      corridor quorum safety + liveness
  Ltp/Commitment.lean  constant-size commitment
  Ltp/Audit.lean       #print axioms for every headline theorem
  verify.sh            build + hole scan + axiom audit
```

## Why `verify.sh` and not just `lake build`

A file containing `sorry` compiles — with a warning that is easy to lose
in CI output. `verify.sh` adds two gates on top of the build: it scans
sources for `sorry`/`admit` (excluding comments, since this README and
`Audit.lean` both discuss the word), and it checks that no theorem depends
on `sorryAx`, which catches a hole reached through any import. The gate is
negative-tested: adding a `sorry` makes it exit non-zero.

Expected axiom base is at most Lean's standard three
(`propext`, `Classical.choice`, `Quot.sound`); today the proofs use only
`propext` and `Quot.sound`, and several are axiom-free.
