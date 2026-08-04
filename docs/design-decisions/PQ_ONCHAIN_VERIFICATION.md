# On-Chain PQ Verification — EIP/ERC Strategy for the LTP Bridge

**Status:** analysis + recommendation, 2026-08-04. No protocol change is
committed by this document.

**Question this answers:** can we author an EIP, or amend an existing one,
that unblocks on-chain verification for the LTP PQ bridge?

**Short answer:** yes, and the highest-leverage move is *not* a new EIP.
A core EIP for exactly this already exists and is actively being shaped
([EIP-8051][8051], ML-DSA verification precompiles). As drafted it cannot
verify a single LTP signature, for three independent reasons. Getting
those three fixed in a draft that is still open is worth more than any
standard we could author from scratch, and it is the only path that makes
the fast path cryptographic rather than administrative.

## 1. What is actually blocked today

The bridge's ML-DSA verification does not happen on-chain, because it
cannot. Two places in the deployed system are shaped around that absence:

- `LTPAnchorRegistry.anchorWithBinding` (`contracts/src/LTPAnchorRegistry.sol:355`)
  stores a `bindingStatementHash` and a `bindingSignatureHash` and states
  in its own NatSpec that "the relayer MUST have verified the owner's
  ML-DSA signature off-chain before calling this; the registry stores
  hashes for any future dispute." The chain records an accusation
  surface, not a verification.
- `ZKBridgeVerifier` exists to route around the same gap by proving
  ML-DSA verification in a ZK circuit instead. Per
  [`BRIDGE_TRUST_MODEL.md`](../BRIDGE_TRUST_MODEL.md) §2, the live
  deployment is `MODE_SIMULATED` and there is no production-grade ML-DSA
  proving system to switch to — an industry-wide gap, not an LTP one.

So both of the bridge's finality paths degrade to trusting a key, and
both do so for the same root cause: **the EVM has no way to check an
ML-DSA signature.** A verification precompile collapses that root cause
directly, and does it without depending on ML-DSA ZK proving ever
becoming production-grade.

## 2. The landscape, as of 2026-08

| Proposal | What it does | Status | Relevance to us |
|---|---|---|---|
| [EIP-8051][8051] | ML-DSA verify precompiles at `0x12` (FIPS-204/SHAKE256) and `0x13` (`ML-DSA-ETH`, Keccak-PRNG), 4500 gas | Draft, Core | **The unlock.** Unusable by LTP as drafted — see §3 |
| [EIP-8052][8052] | Falcon-512 verify precompiles | Draft, Core | Not our scheme; useful precedent for split hash/core precompiles |
| [EIP-7932][7932] | Registry + container for secondary signature algorithms (`ALG_TYPE`) | Draft, Core | EIP-8051 registers `0xD1`/`0xD2` here; account-level, not contract-level |
| [EIP-8141][8141] | Frame transaction / native AA, signature agility | Draft, targeted at Hegota (H2 2026) | Lets accounts rotate to PQ; does not help a *contract* verify a bridge attestation |
| [ERC-7913][7913] | `verify(bytes key, bytes32 hash, bytes signature) → bytes4` verifier contracts; signer = `verifier ‖ key` | **Final** | The interface we should conform to. Ships today, no fork needed |
| [EIP-2537][2537] | BLS12-381 precompiles | Live (Pectra) | Already usable by the threshold-BLS committee path |
| [EIP-7623][7623] | Calldata floor cost, 10/40 gas per token | Live (Pectra) | Dominates every cost estimate below |

## 3. Why EIP-8051 as drafted cannot verify an LTP signature

Three independent blockers. Any one of them is disqualifying.

### 3.1 Parameter set: ML-DSA-44 only

EIP-8051 fixes `k=4, l=4, η=2` — ML-DSA-44, NIST Level II. LTP's default
profile is **ML-DSA-65** (Level 3; `vk` 1952 B, `sig` 3309 B — see
`src/ltp/primitives.py:444`), with **ML-DSA-87** (Level 5) available via
`SecurityProfile`. Nothing in LTP runs at Level II, and dropping to it is
not an option for the compliance surface the protocol targets
(CNSA 2.0 specifies ML-DSA-87 for national-security systems; the
FedRAMP High evidence under `docs/compliance/fedramp-high/` is built on
the Level 3+ profile).

### 3.2 Message length: 32 bytes only

The precompile takes a fixed 32-byte message. LTP signs structured
payloads: `SignedTreeHead.signable_payload()` is 56 bytes
(`sequence ‖ tree_size ‖ timestamp ‖ root_hash`), and the forward-looking
`canonical_bytes()` path is longer and domain-tagged
(`src/ltp/merkle_log/sth.py:52-88`). **No existing LTP signature is
verifiable by a 32-byte-message precompile**, independent of the
parameter-set problem. Fixing this needs either variable-length `M`
support in the EIP, or a new LTP signing lane that signs a 32-byte
domain-separated digest (see §6).

### 3.3 No `ctx` parameter — a security bug, not just an inconvenience

FIPS-204 defines `ML-DSA.Sign(sk, M, ctx)` with a context string of up to
255 bytes, precisely so that signatures from different applications are
not interchangeable. EIP-8051's input is message ‖ signature ‖ public key
with no `ctx`, which pins every verification to `ctx = ""`.

Combined with §3.2, that means the precompile verifies *bare 32-byte
digests with no domain separation*. Any ML-DSA key used with it produces
signatures that are simultaneously valid for every other protocol that
happens to present the same 32 bytes. For a bridge, that is a
cross-protocol replay primitive handed out by the platform. LTP's own
domain separation currently lives *inside* the signed payload
(`DOMAIN_STH_SIGN` via `CanonicalEncoder`), which a 32-byte-digest
interface discards.

## 4. The cost argument (and why it favours us)

EIP-8051 advertises 4500 gas. That number is close to irrelevant: the
public key and signature travel as calldata, and since [EIP-7623][7623]
calldata-heavy transactions pay a floor of 40 gas per non-zero byte
(`TOTAL_COST_FLOOR_PER_TOKEN = 10`, tokens = `zero + 4 × nonzero`). PQ key
and signature material is effectively uniformly random, so treat every
byte as non-zero.

Per verification, calldata only:

| Shape | pk (B) | sig (B) | total (B) | standard (16/B) | **7623 floor (40/B)** |
|---|---:|---:|---:|---:|---:|
| **8051 as drafted** — ML-DSA-44, expanded pk | 20,512 | 2,420 | 22,964 | 367,424 | **918,560** |
| ML-DSA-44, FIPS-204 compact pk | 1,312 | 2,420 | 3,764 | 60,224 | **150,560** |
| ML-DSA-65, expanded pk | 36,896 | 3,309 | 40,237 | 643,792 | **1,609,480** |
| **ML-DSA-65, FIPS-204 compact pk** | 1,952 | 3,309 | 5,293 | 84,688 | **211,720** |
| ML-DSA-65, compact + 32-B key handle | 32 | 3,309 | 3,373 | 53,968 | **134,920** |

(32-byte message included in each total. Expanded pk for ML-DSA-65 is
`Â` 30 polys × 1024 B + `t₁` 6 × 1024 B + `tr` 32 B = 36,896 B.)

Two conclusions fall out:

1. **The 4500-gas precompile price is 0.5% of the real cost.** The
   expanded-public-key encoding costs ~919k gas of calldata per
   verification. A 5-of-n operator attestation is ~4.6M gas — a seventh
   of a 30M block, per anchor.
2. **Level 3 with compact keys is 4.3× cheaper than Level 2 with
   expanded keys** (211,720 vs 918,560). The usual objection to
   supporting higher security levels — "the bigger parameter sets are
   too expensive on-chain" — is false under the dominant cost term. The
   public-key *encoding* choice matters roughly an order of magnitude
   more than the security level does.

That second point is the argument to lead with: we are not asking the
EIP to accept a more expensive option, we are pointing out that the
option it currently mandates is the expensive one.

## 5. Recommendation — three moves, in priority order

### Move 1 (now): file review comments on EIP-8051

Highest leverage by a wide margin. The draft is open, the authors are
responsive, and the [discussion thread][8051-disc] already has unresolved
questions about gas methodology and the undefined `lookup_pubkey` /
`pubkey_hash` mechanism. We arrive with a deployed contract that had to
degrade to hash-recording because this precompile does not exist, plus
the cost table in §4. That is the kind of implementer feedback these
drafts are short of.

Prepared comment: [`docs/eips/eip-8051-ltp-feedback.md`](../eips/eip-8051-ltp-feedback.md).
Asks, in order: compact FIPS-204 keys, `ctx` support, ML-DSA-65/87
parameter sets, variable-length messages, batch verification.

### Move 2 (parallel): pursue L2 adoption ahead of L1

The bridge lives on Base Sepolia, not L1. L2s have shipped verification
precompiles years ahead of L1 before — RIP-7212 (secp256r1) is the
precedent, and it reached production on several rollups long before any
L1 equivalent. An ML-DSA precompile on a rollup is a much shorter path
than L1 core-dev consensus, and it is where our contracts already are.
This is worth raising with Base directly once EIP-8051's encoding
questions settle; pushing for adoption of a spec we know we cannot use
would be premature.

### Move 3 (we control this): publish the ERC and conform to it

An ERC cannot make ML-DSA verification cheap — only a precompile can, and
a pure-Solidity ML-DSA-65 verify is off by orders of magnitude. What an
ERC *can* do is fix the integration surface now, so that the day a
precompile lands on any chain we deploy to, adoption is a constructor
argument rather than a redesign — and so LTP's PQ signers are usable from
ERC-1271 / ERC-4337 accounts and multisigs through a standard interface
instead of a bespoke one.

Draft: [`docs/eips/erc-draft-mldsa-verifier.md`](../eips/erc-draft-mldsa-verifier.md).
It is an [ERC-7913][7913] verifier profile for ML-DSA that pins the key
encoding to FIPS-204, mandates a non-empty `ctx` for domain separation,
and specifies precompile delegation with an explicit "unavailable →
revert" rule so a chain without the precompile fails closed rather than
silently accepting.

### What not to do

**Do not author a competing core EIP for ML-DSA verification.** EIP-8051
occupies that slot with named authors and active review. A second draft
splits reviewer attention and reads as territorial rather than
technical. Everything we need is reachable as amendments to it.

## 6. Our side of the work

Three of these are prerequisites for using *any* ML-DSA precompile,
regardless of how the EIP discussion lands, and none of them are blocked
on it:

1. **A pre-hashed anchor attestation lane.** A signing path that signs a
   32-byte domain-separated digest, so anchors are verifiable by a
   digest-oriented precompile. Additive — existing STH signatures keep
   their current payload encoding, per
   [`STABILITY_PROMISES.md`](../STABILITY_PROMISES.md). This is the
   single largest piece of work and it should not wait on EIP-8051.
2. **A `ctx` parameter on the ML-DSA backend.** `MLDSA.sign/verify`
   (`src/ltp/primitives.py:490,508`) currently expose no context string,
   so LTP is implicitly at `ctx = ""`. FIPS-204 supports it; `pqcrypto`
   exposure needs checking before this is scoped.
3. **Decide the registry's key-handle format** — if we argue for a
   32-byte key handle in EIP-8051 (§4), `LTPAnchorRegistry`'s existing
   `signerVkHash` should be the same preimage, not a second convention.

None of these touch `contracts/` yet, which is deliberate: per
`CLAUDE.md`, contract changes need `make contracts-secaudit` green, and
the toolchain for that is not available in this environment.

## 7. Honest limits of this analysis

- The cost table is calldata arithmetic, not measurement. It assumes
  uniformly random PQ material (all bytes non-zero) and does not model
  blob-carrying or L2 data-availability pricing, which changes the
  absolute numbers on Base substantially — though not the *ratios*
  between encodings, which is what the argument rests on.
- EIP-8051's 4500-gas figure is acknowledged by its author as
  preliminary; the real verify cost is not yet settled and could move the
  precompile term from "negligible" to "material". It will not overtake
  the calldata term at these sizes.
- Nothing here changes the live bridge's trust model. Even a shipped
  precompile leaves the optimistic path's discretionary arbitration
  (`BRIDGE_TRUST_MODEL.md` §3) and the zero bonds (§4) exactly as they
  are. This is one root cause of several.

[8051]: https://eips.ethereum.org/EIPS/eip-8051
[8051-disc]: https://ethereum-magicians.org/t/eip-8051-ml-dsa-verification/25857
[8052]: https://eips.ethereum.org/EIPS/eip-8052
[7932]: https://eips.ethereum.org/EIPS/eip-7932
[8141]: https://eips.ethereum.org/EIPS/eip-8141
[7913]: https://eips.ethereum.org/EIPS/eip-7913
[2537]: https://eips.ethereum.org/EIPS/eip-2537
[7623]: https://eips.ethereum.org/EIPS/eip-7623
