# Validator Compute Incentives — Compute In, Proofs Up, Stablecoins Out

> **Status:** adopted mechanism design + first implementation. This document
> resolves the question `DEFERRED_TOKEN_ARCHITECTURE.md` §4 deliberately left
> open — *"the mechanics of how bonded stablecoin collateral converts into
> chain consensus rights"* — and specifies the payment loop that makes running
> validator compute economically rational before (and without) any native
> token. Companion code: `src/ltp/incentives.py` in this repo and
> `crates/suwappu-precompiles/src/rewards.rs` in `suwappu-dag`.

## 1. Why this exists

The chain cannot launch without validators, and validators are compute:
machines that store erasure-coded shards, serve them under an SLA, sign
ML-DSA/BLS attestations, and (on the DAG side) order certificates. Nobody
runs that hardware for free, and per `DEFERRED_TOKEN_ARCHITECTURE.md` we do
not pay them in a token that doesn't exist yet and whose value would be
speculative if it did.

So the loop is:

```
  users pay commitment/bridge fees in stablecoin
        │
        ▼
  operators run compute (store, serve, attest, order)
        │
        ▼
  infrastructure produces PROOFS of that compute
  (PDP storage audits, SLA-checked serving, quorum
   signature membership, uptime probes, cert counts)
        │
        ▼
  proof-gated settlement pays operators in stablecoin
  — every payout backed by stablecoins that exist
        │
        ▼
  operators bond stablecoin collateral → that bonded
  capital is what secures the bridge today and rents
  itself to Authority/Validator Ring security at launch
```

Censorship resistance is the product of this loop, not an add-on: a
permissionless operator set only materializes if strangers can profitably
join, and strangers can only trust the pay if the pay is provably backed.
That is why both halves below refuse to fabricate value — no unbacked mint,
no payout the pool doesn't hold.

## 2. The two invariants

Everything else is parameterization. These two are the design:

**I1 — Proof-gated pay.** Work that is not proven is not paid. On the LTP
side a `MeteredWorkReport` with zero passed PDP audits earns zero, and pay
scales with the audit pass ratio. On the DAG side a `ComputeReceipt` with
zero uptime samples earns zero, and every receipt field is an observation
made by infrastructure the provider does not control (probes, committed
rounds, quorum membership, SLA-checked retrievals).

**I2 — Backed pay.** A payout exists only if the stablecoin backing it does.
On the LTP side the `StablecoinLedger` can only pay out what was actually
deposited (fees + funded budgets); when claims outrun the pool, settlement
pays pro-rata and *carries* the shortfall as a claim — it never fabricates
balance. On the DAG side reward mints go through
`reserve::mint_with_coverage`, which evaluates the §8.3 reserve-coverage
predicate at the **projected post-mint** outstanding supply and fails
closed: stale attestation, or reserves that wouldn't cover the new supply,
and nothing mints; the epoch stays retryable until a fresh passing
attestation lands.

## 3. What is implemented where

### 3.1 This repo — `src/ltp/incentives.py`

The whitepaper §5.5 interfaces (`NodeIncentive`, `CommitmentPricing`,
`AdmissionControl`), implemented for the stablecoin-native deployment.
This is the rewrite `UNIFIED_TOKENOMICS.md` §2.2 flagged, done against the
deferred-token architecture instead of SUWP:

| §5.5 interface | Implementation | Backing |
|---|---|---|
| `NodeIncentive.compensate/slash` | `StableNodeIncentive` | metered claims paid from `StablecoinLedger`; slashes taken from the operator's stablecoin bond into the insurance pool |
| `CommitmentPricing.price/renew` | `StableCommitmentPricing` | §6.4 cost model (size × replication × TTL) in micro-units; collected fees split 80/10/10 operator/insurance/treasury |
| `AdmissionControl.apply/evict` | `StableAdmissionControl` | stablecoin bond ≥ `min_bond_micro`, storage proof verified by an injected verifier (PDP-compatible); fault evictions forfeit the bond to insurance, voluntary exits refund it |

Key differences from the legacy `ltp/economics.py` (which stays in place
for comparison and its test surface, but models the superseded native-token
world): no inflation and no bootstrap emission (a bootstrap budget is
**funded**, via `fund_incentive_budget`, not minted), and no burn share (you
cannot burn USDC; the burn share became the treasury share, matching the
suwappu-dag §8.3 waterfall of counterparty → insurance → treasury).
Slashing severity tiers are imported from `ltp.economics` so the two models
stay comparable.

The module is deliberately not re-exported from `ltp.__init__` yet — per
`docs/STABILITY_PROMISES.md` that keeps it private and iterable until the
parameterization survives a testnet cycle. Nothing touches the frozen
`LTP-corridor-v1` wire format.

### 3.2 suwappu-dag — `crates/suwappu-precompiles/src/rewards.rs` (+ `reserve.rs`)

Two pieces:

1. **`reserve::mint_with_coverage`** — closes the follow-up DAG-S14 left
   open ("mint integration is a follow-up"): the §8.3 breaker bound into
   the §8.2 issuer mint surface, evaluated at projected post-mint
   outstanding. Any stablecoin-denominated mint should go through it.
2. **`rewards::RewardSettlement`** — per-epoch settlement:
   `ComputeReceipt`s (certificates signed, uptime samples, corridor
   attestations, DA bytes served) are priced under `RewardParams`, clamped
   pro-rata to a hard `epoch_budget`, gated through `mint_with_coverage`,
   and returned as a recipient list shaped for the substrate's existing
   `Intent::DistributeRewards` (which already has per-epoch replay guards,
   atomic credits, and reserved-address rejection — the rail existed;
   this computes the amounts nothing was computing). Uptime gating mirrors
   the public testnet points contract (`docs/testnet/POINTS.md`): ≥99%
   full rate, ≥95% half rate, below that nothing.

Exit gate: `tests/proptest_compute_rewards.rs`, 4 properties (conservation
+ reserve backing; fail-closed coverage; work monotonicity; epoch replay),
verified at `PROPTEST_CASES=10000 --release` and spot-checked at 200k.

### 3.3 The inference connection

The revenue engine that funds this loop at scale is metered model
inference — see [`INFERENCE_REVENUE.md`](INFERENCE_REVENUE.md):
customers buy completions against the network's own model through the
gateway (`src/ltp/gateway/routers/inference.py`), and every settled
request deposits its fee into the same `StablecoinLedger` split that
pays providers here.

### 3.4 The bridge connection

Bridge volume is the revenue that makes the loop self-sustaining: bridge
fees are stablecoin-denominated by construction (CCTP-shaped, per
`DEFERRED_TOKEN_ARCHITECTURE.md` §3.1) and route into the same
`StablecoinLedger` fee split. Attestor/operator bonds on the bridge are
the same collateral pool as `StableAdmissionControl` bonds — one operator
identity, one collateral pool, across bridge and chain.

## 4. Parameter posture (defaults, not gospel)

| Parameter | Default | Note |
|---|---|---|
| Storage rate | $0.02 / GiB-month | `IncentiveConfig.price_per_gib_month_micro` |
| Serving rate | $0.01 / GiB | `price_per_gib_served_micro` |
| Fee split | 80% operators / 10% insurance / 10% treasury | no burn |
| Operator bond | $1,000 | `min_bond_micro`; a testnet zeroing this must say so publicly (onboarding-plan finding B6) |
| DAG epoch budget | `RewardParams.epoch_budget` | hard ceiling; oversubscription scales pro-rata |
| Uptime gates | ≥99% → 1.0×, ≥95% → 0.5×, else 0 | mirrors POINTS.md |

All of these are meant to be re-derived from real testnet data. The
invariants in §2 are not.

## 5. What this does NOT solve (the honest list)

- **Receipt production is off-consensus today.** `ComputeReceipt`s and
  `MeteredWorkReport`s are produced by probes, audit schedulers, and the
  validator-program daemon — trusted infrastructure, not consensus. The
  next hardening step is signing receipts (the corridor attestation and
  PDP machinery already exist to do it) so settlement inputs are
  independently verifiable.
- **Nobody calls the settlement at epoch boundaries yet.** The dag daemon's
  epoch-boundary hook (`EpochState::boundary_crossed_by`) is where
  `RewardSettlement::settle_epoch` + `Intent::DistributeRewards` get wired;
  that touches `suwappu-node` and is deliberately a separate change.
- **The on-chain contract surface.** `LTPNodeRegistry.sol` (operator
  registration/staking, onboarding-plan blocker B5) still doesn't exist,
  and the live bridge bonds are still 0 ETH (not even stablecoin). Both
  need the `contracts/` gate (`make contracts-secaudit`, upgrade plan under
  `plans/`, work-account CODEOWNERS) and are out of scope here.
- **Network reachability and admission transport** — blockers B1–B4 of
  `docs/plans/2026-08-04-external-validator-onboarding.md` are unchanged;
  this document supplies the economics those phases plug into, not the
  plumbing.

## 6. Reading order

1. `DEFERRED_TOKEN_ARCHITECTURE.md` — why stablecoin-native, no token
2. This document — the loop and its two invariants
3. `src/ltp/incentives.py` + `tests/test_incentives.py` — LTP half
4. `suwappu-dag`: `crates/suwappu-precompiles/src/rewards.rs`,
   `src/reserve.rs` (`mint_with_coverage`),
   `tests/proptest_compute_rewards.rs` — chain half
5. `docs/plans/2026-08-04-external-validator-onboarding.md` — the
   operational phases this economics slots into
