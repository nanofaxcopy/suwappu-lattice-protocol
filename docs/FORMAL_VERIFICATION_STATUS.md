# Formal Verification Status

What's verified, by what tool, and what's not — for outside cryptographic reviewers.

## What's in `docs/formal/`

| Artifact | Tool | Status |
|---|---|---|
| [`ANALYSIS.md`](formal/ANALYSIS.md) | (overview doc) | Methodology, attacker model, cryptographic abstractions, query list |
| [`etp-protocol.vp`](formal/etp-protocol.vp) | Verifpal v0.27+ | Symbolic model of the 3-phase COMMIT / LATTICE / MATERIALIZE protocol. **Written but never run** — see below. |
| [`../formal/lean/`](../formal/lean/) | Lean 4 (core, no Mathlib) | **Machine-checked.** Corridor 7-of-9 quorum safety + liveness, and the constant-size commitment invariant. Gated in CI by `.github/workflows/formal.yml`. |

## Machine-checked (Lean 4)

Added 2026-08-15 to cover the threshold-quorum gap this document itself
identified as out of reach for Verifpal. Run `formal/lean/verify.sh`.

| Theorem | Claim |
|---|---|
| `corridor_intersection` | Any two 7-of-9 attestations share ≥ 5 signers (`7 + 7 - 9`) |
| `corridor_safety` | With ≤ 4 Byzantine super-nodes, any two attestations share an **honest** signer — two conflicting attestations cannot both be valid |
| `corridor_liveness` | With ≤ 2 unavailable super-nodes a quorum is still formable; note the asymmetry (safety tolerates 4, liveness only 2) |
| `commitment_size_payload_independent` | On-chain commitment size is independent of payload — the operative content of Paper §10.2 |
| `strict_total_unsatisfiable` | No valid envelope totals the pinned `ON_CHAIN_COMMITMENT_BYTES = 1_600`; ML-KEM-768 gives 1,216 and ML-KEM-1024 gives 1,696 |

The proofs use no `sorry` (CI enforces this via an axiom audit, and the
gate is negative-tested). **They are proofs about a model, not about
`src/ltp/`** — cryptographic soundness is assumed, and nothing is
extracted to the running code. Read
[`formal/lean/README.md`](../formal/lean/README.md#what-is-not-proved--read-this-before-citing-these-results)
before citing them.

## ⚠️ The Verifpal model has never actually been run

The line below has said "pending the next release-engineering pass" since
this document was written. Treat every Verifpal row in this file as
**claimed, not established**, until someone runs it and records the
output. This is tracked as a gap rather than quietly presented as
verification.

## What is verified symbolically

The `etp-protocol.vp` model has been written but the official verification run is pending the next release-engineering pass. The queries it asserts (per `ANALYSIS.md`) are:

| Property | Verifpal query | Expected outcome |
|---|---|---|
| CEK confidentiality | `confidentiality? cek` | Attacker cannot learn the content encryption key |
| Content confidentiality | `confidentiality? content` | Attacker cannot learn the plaintext content |
| Commitment authentication | `authentication? Sender -> Receiver: commitment` | Commitment record is bound to the sender's ML-DSA-65 key |
| Sealed key authentication | `authentication? Sender -> Receiver: sealed_key` | Lattice key is bound to the sender's identity |

The attacker is the standard Dolev-Yao active adversary (unbounded sessions, fresh values, full message-modification capability).

## What is NOT in scope of the symbolic model

The Verifpal model intentionally abstracts these components — they are outside the symbolic verifier's reach and must be argued by other means:

| Out-of-scope component | Why | Where it's addressed |
|---|---|---|
| ML-KEM-768 reduction | Verifpal has no native KEM primitive; we model it as DH, which has equivalent confidentiality guarantees in the symbolic setting but a different concrete reduction | NIST FIPS 203, plus `tests/test_acvp_mlkem.py` against ACVP test vectors |
| ML-DSA-65 reduction | Modeled as a generic SIGN primitive | NIST FIPS 204, plus `tests/test_acvp_mldsa.py` |
| Erasure coding `k-of-n` threshold | Information-theoretic; the symbolic model cannot express "any `k` shares determine the secret, any `k-1` do not" | `tests/test_erasure.py` plus the constructive proof in `docs/WHITEPAPER.md` §5 |
| Side-channel resistance of the implementation | Symbolic verifiers don't model timing or power channels | `docs/security/audits/internal/SECURITY_REVIEW-2-24-2026.md` §4, plus the `assert_bls_production()` gate in `src/ltp/bls.py` that blocks the unaudited `py_ecc` keygen fallback under `LTP_ENV=production` |
| Commitment-network topology | Shard placement, retrieval, gossip — infrastructure-level, not protocol-level | `docs/THREAT_MODEL.md` §3 (Threat Sources) and §4.D (DoS) |

## Other formal artifacts

The following adjacent reviews have already been published:

| Document | What it covers |
|---|---|
| [`security/audits/internal/SECURITY_REVIEW-2-24-2026.md`](security/audits/internal/SECURITY_REVIEW-2-24-2026.md) | Formal security review of the full protocol and implementation, dated 2026-02-24 |
| [`security/audits/internal/001-lattice-key-shard-exposure.md`](security/audits/internal/001-lattice-key-shard-exposure.md) | Attack-chain analysis of the lattice-key shard exposure surface, with Option A-D mitigation comparison |
| [`security/audits/external/whitepaper-reviews/001/001-Mathematical-Review.md`](security/audits/external/whitepaper-reviews/001/001-Mathematical-Review.md) | Math review #1 (2026-03-19) — flagged 3 critical errors; all resolved per review #2 |
| [`security/audits/external/whitepaper-reviews/002/002-Mathematical-Review.md`](security/audits/external/whitepaper-reviews/002/002-Mathematical-Review.md) | Math review #2 (2026-03-27) — confirms prior errors fixed; 5 open issues for future work |
| [`THREAT_MODEL.md`](THREAT_MODEL.md) | STRIDE + PQC-specific threat model |

## How to run the symbolic verification yourself

```bash
# install Verifpal (Go-based)
brew install verifpal
# or: go install github.com/symbolicsoft/verifpal@latest

# run from the repo root
verifpal verify docs/formal/etp-protocol.vp
```

The expected output is one line per query with a `verified` or `attack` verdict. Open an issue with the `verifpal-output` label if you see anything other than `verified` so we can update the documented status.

## What would strengthen the case

Items the maintainers know are missing and welcome contributions on:

- Running the Verifpal model and recording the output (see the warning above — this is the cheapest outstanding item)
- ~~A **Tamarin** or **ProVerif** model of the corridor 7-of-9 BLS attestation flow~~ — the *quorum* half of this is now covered by the Lean proofs above. A symbolic model is still wanted for the parts Lean does not touch: aggregate-signature unforgeability under a Dolev-Yao attacker, and the PoP exchange (LTP-A-015)
- A **Certora** prover spec for `LTPAnchorRegistry.sol` covering sequence monotonicity, entity-signer binding, and the UUPS upgrade-admin gate
- A **`hypothesis`**-based fuzz harness for `src/ltp/corridor/wire.py` deserialization (one shipped in PR #8 as `tests/test_corridor_wire_validation.py` but it's table-driven; property-based would catch more edge cases)
- A side-channel evaluation of the `py_ecc` keygen path, or a contributed Rust binding for an audited constant-time KEM library

If you're a cryptographic reviewer reading this and want a longer evidence pack, file an issue with the `formal-review-request` label and reference the property you want strengthened.
