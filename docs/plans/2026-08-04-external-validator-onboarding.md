# Plan — Letting external operators join the testnet

**Date:** 2026-08-04
**Status:** analysis + proposed sequencing. Nothing here is committed work.
**Scope decision required:** see §4 before implementation starts.

## 1. What "validator" means in this repo

Two different roles get called "validator", and only one of them is this
repo's to open up:

- **DAG consensus validator** — block ordering, validator-ring consensus.
  Lives in [`suwappu-dag`](https://github.com/Suwappu-Labs/suwappu-dag),
  not here. `README.md:125` is explicit: "DAG ordering and validator-ring
  consensus live in `suwappu-dag`." Opening that up is a suwappu-dag plan.
- **LTP node operator / attestor** — runs the Merkle log, signs STHs with
  an ML-DSA-65 key, serves and stores erasure-coded shards, optionally
  anchors on-chain, and (once committees are live) holds a threshold-BLS
  share. **This is the role this plan is about.**

Below, "operator" means the second one. Where a doc or an issue says
"validator" and means the first, it needs redirecting to the other repo.

## 2. Where things actually stand

The pieces are in better shape than the gap list in §3 suggests, which is
why this is a sequencing problem rather than a from-scratch build:

| Piece | State |
|---|---|
| Node daemon, ML-DSA-65 authenticated handshake, gossip discovery | Implemented — `src/ltp/node/main.py`, `handshake.py`, `gossip.py` |
| Admission state machine (m-of-n endorsement) | Implemented as a library — `src/ltp/node/admission.py` |
| Writer lifecycle (PENDING → sponsors → PROBATION → ACTIVE, suspend/revoke/expiry) | Implemented as a library — `src/ltp/execution/writer_registry.py` |
| Committee formation, epochs, eviction, standby | Implemented — `src/ltp/execution/committee/` |
| Threshold DKG + BLS signing | Implemented — `src/ltp/execution/committee/dkg/` |
| Deployment surface (Docker, Helm, k8s, preflight, Grafana dashboards) | Implemented — `deploy/` |
| Operator runbook | Written — `docs/OPERATOR_RUNBOOK.md` |
| On-chain signer authorization | Implemented, admin-gated — `LTPAnchorRegistry.registerSigner`, `contracts/src/LTPAnchorRegistry.sol:281` |

The protocol work is largely done. What is missing is everything that
turns it from a library into a network someone outside the team can
reach and be admitted to.

## 3. Blockers, in dependency order

### B1. There is no reachable network

`suwappu.network` does not resolve. Verified 2026-08-04 from a sandbox
whose DNS resolves `github.com`, `base.org`, and `eips.ethereum.org`
normally — so this is the domain's state, not a local artifact.

Every endpoint anywhere in the repo is a placeholder:

- `docs/OPERATOR_RUNBOOK.md:157-161` — seed peers are
  `etp-us-east-1.example.com`, `etp-eu-west-1.example.com`.
- `docs/DEPLOYED_CONTRACTS.md:100-105` — verification commands take
  `$SUWAPPU_RPC_URL` / `$BASE_SEPOLIA_RPC_URL` from the environment; no
  public URL is recorded for SUWAPPU Testnet (chain ID 103115120).
- `https://rpc.testnet.suwappu.network` appears only in test fixtures
  (`tests/test_node_bootstrap.py:812,842,873`).

**A prospective operator today has no address to point a node at.** Every
other item on this list is downstream of that. Note that the Base Sepolia
half of the deployment *is* reachable — Base Sepolia is a public network —
so anchoring against Base Sepolia is testable by outsiders even while
the SUWAPPU chain is not.

### B2. Admission is a library, not a service

`NodeAdmissionManager` is in-process and in-memory. It is instantiated
nowhere in `src/` — only in `tests/test_gate_integration.py:94` and as an
optional gate parameter in `src/ltp/commitment.py:1002`. `node/main.py`
does not reference it.

Consequences: no endpoint for an applicant to apply to, no way for
operators to submit endorsements across a network, no persistence (state
is lost on restart), and no shared view — each node would have its own
private opinion of who is admitted.

### B3. Committee machinery is not wired into the node

`WriterRegistry` is instantiated only in tests
(`tests/test_writer_registry.py`, `test_committee_e2e.py`,
`test_committee_epoch.py`, `test_writer_epoch.py`,
`test_gate_5_6_closure.py`). `node/main.py` contains no reference to
writers or committees. Same in-memory, no-persistence issue as B2.

### B4. DKG has no real transport

`src/ltp/execution/committee/dkg/transport.py` defines a `DKGTransport`
`Protocol` and a `FakeDKGTransport` that passes messages through Python
lists. There is no network implementation. **Threshold DKG cannot
currently run between two separate machines**, which means a multi-party
committee including an external operator cannot be formed at all. This is
the hardest blocker and the one most likely to be underestimated —
DKG transport needs authenticated, ordered, per-recipient private
delivery of shares plus broadcast for commitments and complaints.

### B5. The on-chain node registry doesn't exist

`src/ltp/backends/ethereum.py:17` documents `LTPNodeRegistry.sol` as
"node admission, staking, eviction", and `:513` comments "Simulate
contract call: `LTPNodeRegistry.registerNode{value: stake}(...)`".
`docs/design-decisions/COMMITMENT_NETWORK_OPTIONS.md:99,110` costs it out
at ~120K gas per registration. **No such contract exists in
`contracts/src/`.** The on-chain admission and staking surface is
simulated end to end.

### B6. "Stake deposited" deposits nothing

`AdmissionState.ADMITTED` is documented as "Stake deposited"
(`admission.py:45`) and `backends/base.py:88` defaults
`min_stake_wei = 0`. This is the same shape as the bridge's zero bonds
(`BRIDGE_TRUST_MODEL.md` §4): the mechanism exists, the parameter makes
it inert. For a testnet that is defensible; it should be *stated* as
intentional rather than left to look like a misconfiguration.

### B7. Onboarding has no third-party-facing path

`docs/OPERATOR_RUNBOOK.md:7` declares its audience as "SREs, node
operators, on-call engineers" — internal staff who already have peers,
KMS, and certificates. There is no document that answers: how do I ask to
join, what do I publish (an ML-DSA-65 vk is 1952 bytes — in what format,
where), who endorses me, how long does it take, what do I get told when
I'm rejected, and what am I promising in return (uptime, availability,
key custody)?

Seed keys are pinned trust-on-first-use (`OPERATOR_RUNBOOK.md:164`),
which is acceptable within a team that confirms fingerprints out of band
and weak for a public joiner who has no out-of-band channel.

## 4. The decision that gates the design

**Permissioned-by-endorsement, or open-with-stake?**

Both are already half-built, which is part of why this is unresolved:
`NodeAdmissionManager` implements m-of-n operator endorsement, and
`WriterRegistry` implements sponsor-threshold + probation
(`writer_registry.py:143`, `sponsor_threshold = 2`,
`probation_epochs = 10`). Meanwhile `backends/base.py` and the
non-existent `LTPNodeRegistry` assume a staking model.

**Recommendation: permissioned-by-endorsement for this testnet, and say
so publicly.** Reasons:

1. It is what the code implements. The staking path needs a contract that
   does not exist (B5) and an economic parameterization nobody has set
   (B6).
2. The trust model is already discretionary — bridge disputes resolve by
   admin or arbiter ruling (`BRIDGE_TRUST_MODEL.md` §3). Opening operator
   admission permissionlessly while dispute resolution stays
   discretionary would advertise a decentralization the system doesn't
   have.
3. It is reversible. Endorsement-gated admission can add a stake
   requirement later; a public "anyone can join" testnet that later
   restricts is a much worse announcement.

The cost of this choice is honesty about what the testnet is: an
invite-based operator set, not a permissionless network. Phases 1-3 below
are identical under either model, so the decision does not block starting.

## 5. Proposed sequencing

Phases 1-3 are prerequisites under any model. Phase 4 is where §4's
decision bites.

### Phase 1 — Make the network reachable

Nothing else is testable by an outsider until this is true.

- Stand up and publish a SUWAPPU Testnet RPC endpoint, or document
  explicitly that external operators run against **Base Sepolia only**
  and that the SUWAPPU chain is internal. Either is a fine answer; the
  current silence is not.
- Publish a network descriptor — chain ID, RPC URL, registry proxy
  address, at least two seed peers with pinned ML-DSA-65 vk fingerprints,
  and the genesis/first-STH reference a joiner syncs from.
- Replace `example.com` placeholders in `OPERATOR_RUNBOOK.md` §4 with
  real seeds, or mark them explicitly as illustrative.

**Done when:** someone outside the team can start a node from the public
docs and complete a handshake with a seed peer.

### Phase 2 — Persist and expose admission

- Give `NodeAdmissionManager` a durable store. The node already carries a
  storage backend abstraction (`NodeConfig.storage_backend`,
  `memory | sqlite | filesystem`) — reuse it rather than inventing one.
- Wire it into `node/main.py` and expose it over the gateway: submit
  application, list pending, submit endorsement, query status.
  Endorsements are already domain-separated ML-DSA signatures
  (`DOMAIN_NODE_ENDORSEMENT`, `admission.py:107`) so the wire format is
  mostly settled; what's missing is transport and storage.
- Decide how admission state is shared between operators. Gossiping
  signed endorsements is the natural fit given `gossip.py` exists;
  anchoring the admitted set on-chain is the stronger option and is the
  same question as Phase 4's.

**Done when:** an applicant can apply over the network, two operators can
endorse from separate machines, and the resulting state survives a
restart of every node involved.

### Phase 3 — Real DKG transport

The largest single item. Needed before an external operator can hold a
threshold share.

- Implement `DKGTransport` over the existing authenticated peer channel:
  broadcast for commitments and complaints, authenticated per-recipient
  delivery for shares.
- Decide the failure semantics — timeouts, complaint windows, and what
  happens when a participant drops mid-ceremony — and test them against
  a partitioned multi-process harness, not `FakeDKGTransport`.
- Wire `WriterRegistry` + `CommitteeFormation` into the node (B3).

**Done when:** a DKG ceremony completes across three separate processes
on separate hosts, one of which is not operated by the core team, and the
resulting group key verifies a threshold signature.

### Phase 4 — Admission of record

Where §4's decision applies.

- *Permissioned:* document the endorsement process as the authoritative
  admission path with a stated review SLA, and anchor the admitted
  operator set so it is externally auditable. `registerSigner` stays
  admin-gated — that is the honest shape of an invite-based network.
- *Staked:* write `LTPNodeRegistry.sol` (B5), set a non-zero
  `min_stake_wei` (B6), and route admission through it. Note the
  `contracts/` gate — `make contracts-secaudit` must be green, and
  CODEOWNERS routes contract changes to the work account.

### Phase 5 — Joiner-facing documentation

- An operator onboarding guide aimed at someone with no prior contact:
  what to run, what to publish, how to request endorsement, expected
  timeline, and what the operator agreement is.
- A vk publication format. 1952-byte ML-DSA-65 keys need a canonical
  encoding and fingerprint convention for out-of-band confirmation; the
  runbook's `ml-dsa65:base64:...` form (`OPERATOR_RUNBOOK.md:158`) is a
  reasonable starting point but is not specified anywhere.
- Retarget `OPERATOR_RUNBOOK.md`'s audience line, or split internal
  operations from third-party onboarding.

## 6. Cost note

Phase 3 is the schedule risk. Phases 1, 2, and 5 are mostly plumbing and
writing over machinery that already exists and is tested. Phase 3 is new
distributed-systems code with adversarial failure modes, and "DKG works
in tests with a fake transport" is a long way from "DKG survives a
participant dropping mid-ceremony across the public internet." If the
goal is external operators running *nodes* soon, Phases 1, 2, and 5 get
there. If the goal is external operators holding *threshold shares*,
Phase 3 is on the critical path and should be scoped separately.

## 7. What this document is not

- Not an implementation. No code changes accompany it — the environment
  it was written in has neither `forge` nor the Python test
  dependencies (`pqcrypto`, `pytest`), so nothing shipped here could
  have been verified, and untested changes across five subsystems would
  be worse than none.
- Not a decision on §4. That is a product call.
- Not a security review of the resulting network. Admitting external
  operators changes the threat model — `THREAT_MODEL.md` assumes an
  operator set the team controls, and every phase above widens it.
  That review should happen before Phase 4, not after.
