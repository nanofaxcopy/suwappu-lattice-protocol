# EIP-8051 review comment — prepared, not yet posted

**Target:** <https://ethereum-magicians.org/t/eip-8051-ml-dsa-verification/25857>
**Prepared:** 2026-08-04. **Status: DRAFT — do not post without sign-off.**

Posting under the project's name is an external, public act; see §"Before
posting" at the bottom. Background and the reasoning behind each ask:
[`../design-decisions/PQ_ONCHAIN_VERIFICATION.md`](../design-decisions/PQ_ONCHAIN_VERIFICATION.md).

---

## Draft comment text

Implementer feedback, from a protocol that would be an immediate consumer
of this precompile.

We maintain the Lattice Transfer Protocol — post-quantum data transfer
with on-chain anchors, ML-DSA-65 signatures over a CT-style Merkle log,
with a bridge deployed on Base Sepolia. Our anchor registry currently
carries this in its NatSpec:

> the relayer MUST have verified the owner's ML-DSA signature off-chain
> before calling this; the registry stores hashes for any future dispute

That degradation exists solely because there is no way to check an ML-DSA
signature on-chain. EIP-8051 is the thing that removes it, so we have a
concrete stake in the encoding details. Five points, roughly in order of
how much they matter to us.

### 1. The expanded public key encoding costs ~200× the precompile's gas

The spec takes a 20,512-byte expanded public key (`Â` ‖ `tr` ‖ `t₁` in NTT
domain). Since EIP-7623, calldata-heavy transactions pay a floor of 40 gas
per non-zero byte, and PQ key material is uniformly random, so effectively
every byte is non-zero:

| | pk (B) | sig (B) | total (B) | 7623 floor gas |
|---|---:|---:|---:|---:|
| As drafted (ML-DSA-44, expanded pk) | 20,512 | 2,420 | 22,964 | **918,560** |
| ML-DSA-44, FIPS-204 compact pk | 1,312 | 2,420 | 3,764 | **150,560** |

(32-byte message included in totals.)

The advertised 4500 gas is 0.5% of the delivered cost. We understand the
expanded form exists to skip NTT expansion inside the precompile — but
that trade is being made against a term that is two orders of magnitude
larger. Expanding `Â` from `ρ` is SHAKE128 rejection sampling over `k×l`
polynomials; even priced generously it cannot approach 750k gas.

**Ask:** accept the FIPS-204 encoded public key as an input form, priced
higher than the expanded form to reflect the expansion work. Keep the
expanded form for callers who have somewhere cheap to put 20 KB. Do not
make the expensive form the only form.

### 2. There is no `ctx` parameter, which pins verification to `ctx = ""`

FIPS-204 defines `ML-DSA.Sign(sk, M, ctx)` with a context string of up to
255 bytes, specifically so signatures from different applications are not
interchangeable. The precompile input is message ‖ signature ‖ public key,
with no way to supply one.

Combined with the fixed 32-byte message, the precompile verifies bare
digests with no domain separation: a signature over 32 bytes is
simultaneously valid for every protocol that presents those same 32 bytes.
For bridges and account-abstraction validators — the two most likely first
consumers — that is a cross-protocol replay primitive, and one the
underlying standard already gives you the tool to avoid.

**Ask:** add `ctx` (length-prefixed, ≤255 B) to the input encoding, and
add a Security Considerations paragraph stating that `ctx = ""` with a
caller-chosen 32-byte digest offers no domain separation. If `ctx` is
deliberately out of scope, that paragraph matters more, not less.

### 3. ML-DSA-44 only excludes the compliance-driven deployments

The spec fixes `k=4, l=4` — Level II. Deployments under CNSA 2.0 need
ML-DSA-87; ours runs ML-DSA-65 by default with ML-DSA-87 available, and we
cannot drop to Level II. That is not a preference, it is the constraint
that put us on Level 3+ to begin with.

Anticipating the cost objection — from the same arithmetic as §1:

| | pk (B) | sig (B) | 7623 floor gas |
|---|---:|---:|---:|
| ML-DSA-44, expanded pk (as drafted) | 20,512 | 2,420 | **918,560** |
| ML-DSA-65, FIPS-204 compact pk | 1,952 | 3,309 | **211,720** |

**ML-DSA-65 with compact keys is 4.3× cheaper than ML-DSA-44 as
currently specified.** The higher security level is not the expensive
choice here; the key encoding is. Adopting §1 makes §3 nearly free.

**Ask:** parameterize over `(k, l, η, γ₁, γ₂, τ, β)` for the three FIPS-204
sets, distinguished by input length (compact pk 1312 / 1952 / 2592 B is
unambiguous), or specify sibling precompiles. Per-level gas, since verify
work does scale with `k`.

### 4. The 32-byte message excludes protocols that sign structured payloads

Our Signed Tree Heads sign a 56-byte canonical encoding
(`sequence ‖ tree_size ‖ timestamp ‖ root_hash`); our forward-looking
encoding is longer and domain-tagged. No existing signature we hold is
verifiable under a 32-byte-message interface, independent of §3.

We can add a pre-hashed signing lane, and probably will regardless. But
worth stating explicitly in the EIP: as drafted this precompile is not
"verify an ML-DSA signature", it is "verify an ML-DSA signature over a
32-byte message", and protocols with existing signature corpora cannot
migrate to it — they must re-sign. If that constraint is intentional,
saying so in Rationale saves every implementer the same discovery.

**Ask:** either support a length-prefixed variable-length `M` with
per-byte gas, or specify FIPS-204 §5.4 HashML-DSA (which encodes the hash
function's OID into the signed message, giving pre-hashing a standardized
and domain-separated form). Second option is probably the right one — it
keeps the fixed-size input and closes §2's footgun at the same time.

### 5. `lookup_pubkey` / `pubkey_hash` and the batching case

Following on from SirSpudlington's unresolved question in this thread
about `lookup_pubkey` and EIP-8052's undefined `pubkey_hash`: from our
side these point at a key registry, and the cost table explains why one
matters. A 32-byte key handle in place of an inline public key takes
ML-DSA-65 verification from 211,720 to 134,920 gas of calldata; against
the as-drafted encoding it is a ~7× reduction.

The case sharpens under threshold attestation. A bridge anchor validated
by 5 committee members pays 5× the per-verification calldata:
~4.6M gas as drafted, ~675k with compact keys and handles. The first is
a seventh of a block per anchor; the second is a normal transaction.

**Ask:** if a registry is in scope for this EIP, specify it (including
what `pubkey_hash` commits to — encoded pk or expanded). If it is
deliberately deferred to a separate proposal, say so and drop the
dangling references. Separately, batch verification of `n` signatures
under distinct keys would amortize well and is worth a Rationale note
even if deferred.

### Summary of asks

1. Accept FIPS-204 compact public keys as an input form (biggest win)
2. Add `ctx`, or document the domain-separation gap in Security Considerations
3. Support ML-DSA-65 and ML-DSA-87, distinguished by input length
4. Variable-length `M`, or specify HashML-DSA
5. Resolve or remove `lookup_pubkey` / `pubkey_hash`; note batching

Happy to contribute cost analysis, test vectors from a production
ML-DSA-65 deployment, or a PR against the spec text for any of these.

---

## Before posting

- [ ] Confirm the ML-DSA-87 / CNSA 2.0 claim in §3 against the current
      CNSA 2.0 FAQ rather than secondhand summaries.
- [ ] Re-read the thread for movement since 2026-08-04 — several of these
      may already be resolved in the draft, and repeating a settled point
      wastes the authors' time and our credibility.
- [ ] Decide whether to post under an individual maintainer's account or
      the project's, and who owns follow-up. Feedback that goes quiet
      after the first reply is worse than not filing it.
- [ ] Confirm §1's expansion-cost claim ("cannot approach 750k gas") is
      defensible if challenged — ideally with a benchmark, since the
      authors have been asked for gas methodology already and will
      reasonably hold us to the same bar.
