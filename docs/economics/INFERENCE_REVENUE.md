# Inference Revenue — The Demand Side of the Compute Loop

> **Status:** adopted mechanism + first implementation. Companion to
> [`VALIDATOR_COMPUTE_INCENTIVES.md`](VALIDATOR_COMPUTE_INCENTIVES.md), which
> specifies how proven compute gets *paid*; this document specifies where the
> money *comes from*: selling inference against the network's own model,
> metered per token and settled in stablecoins. Code:
> `src/ltp/inference.py` + `src/ltp/gateway/routers/inference.py`.

## 1. Why inference is the revenue engine

The incentive loop is solvency-bounded by design — providers can only be
paid from stablecoins the ledger actually holds. That makes the funding
question the whole game: commitment fees alone scale with protocol usage,
which is slow to bootstrap. Inference demand is the fee source that scales
*now*: the same operators who run validator compute run the model runtime,
customers pay per token in stablecoins, and every settled request funds the
operator pool that pays the whole provider economy.

```
  customer ──► POST /inference/v1/chat/completions   (pays metered quote)
                     │
                     ▼
  model backend runs (the network's own model runtime)
                     │
                     ▼
  InferenceReceipt: SHA3-256 request/response digests + token counts
                     │
                     ▼
  InferenceMarket.settle ──► StablecoinLedger.deposit_fee
        80% ─► operator pool ─► serving node's claim (paid at epoch
        10% ─► insurance          settlement, solvency-clamped)
        10% ─► treasury
```

One ledger, one operator identity, one solvency invariant — inference
revenue and storage fees share the pool, per the deferred-token
architecture's "one operator economy."

## 2. Billing rules

1. **Metered, not subscription.** Per-million-token input/output rates
   (`InferencePricing`), industry-standard units, quoted before serving.
   Ceiling rounding: a nonzero request never bills zero.
2. **One receipt, one settlement.** Settlement is keyed by `request_id`;
   replays are rejected. Same duplicate-redemption posture as the issuer
   precompile's two-phase burn on the chain side.
3. **Receipts are evidence of the bill, not of the work.** SHA3-256
   digests (the canonical/on-chain hash lane) of the exact request and
   response bodies ride the receipt; the bodies never enter the billing
   path. The receipt verifier is injected — production wires one that
   checks digests against an LTP commitment, so a customer can audit
   "the invoice matches the exchange you attested to, and neither of us
   can revise it after the fact" without anyone shipping the payloads.
   **Read §2.1 before repeating this as a capability claim.**

### 2.1 What the receipt proves — and what it does not

Three claims get conflated in this market, and only the third is ours.
Keeping them apart is a correctness requirement for our own docs and
sales material, not modesty:

| Claim | What it needs | Cost today | Do we have it? |
|---|---|---|---|
| *"This model, with these weights, produced this output for this input."* | zkML, or TEE attestation, or redundant execution with dispute | zkML 10³–10⁶× (impractical for LLMs); H100 confidential computing ~0–7%; Gensyn Verde 2-provider bisection ~2× inference | **No** |
| *"Some real compute happened on real hardware."* | Hardware attestation / proof-of-work-done | Low, but see io.net: attestation proves the GPU exists and nothing about the job | **No** |
| *"The invoice matches the metered events the provider attested to, and neither party can alter it retroactively."* | Signed, committed, append-only billing records | Negligible — one Merkle leaf + one signature per request | **Yes** |

A provider that fabricates meter readings, signs them, and commits them
produces a receipt that commits *perfectly to a lie*. The receipt makes
the provider's claim **immutable and attributable**; it does not make it
**true**. Calling this "verifiable inference" would be false, and a
technical buyer catches it immediately.

What it *is*, honestly stated: **settlement-integrity infrastructure**.
It converts a billing dispute from he-said-she-said into an evidentiary
one, and enables third-party audit and automated reconciliation. That is
a real, unoccupied gap — the surveyed compute networks resolve disputes
by defunding escrow (Akash), by human approval of watermarked output
(Render), or not at all; none publishes a cryptographic trail linking
metered usage → invoice → settlement. It maps to a buyer with existing
budget (procurement, audit, FinOps, regulated deployments) rather than to
the crypto-native "prove the model ran" obsession.

It also **composes** with a verification primitive rather than competing
with one: bind a TEE attestation quote into the receipt leaf and the
receipt inherits a hardware-rooted claim about execution. The receipt is
the settlement layer, and it is only ever as strong as the attestation
bound into its leaves.

**On post-quantum specifically.** ML-DSA on the tree heads protects
against an adversary who can forge signatures on *historical* records —
a store-now-forge-later threat against audit trails with 7–10 year
regulatory retention. That is a coherent and narrow claim. It is *not* a
competitive differentiator in the compute market itself: no competitor's
economic security depends on signature longevity, since escrow, approval
gates, and stake weighting all settle within blocks and are indifferent
to a 2035 adversary. Positioning PQ as protecting *the market* would be
marketing; positioning it as protecting *the durability of the audit
record* is defensible.
4. **Underpay rejected, overpay kept.** What was quoted is what settles.
5. **Evicted nodes serve for free.** A receipt naming an evicted node still
   bills the customer (they got the response) but accrues no claim — the
   operator share stays in the pool for honest providers.

## 3. The gateway surface

`/inference/v1/chat/completions` is OpenAI-shaped, so existing client SDKs
work with a base-URL change. JWT-protected under the gateway's standard
middleware — the JWT subject is the paying customer. The model backend is
deployment-injected (`app.state.inference_backend`): the gateway never
calls a third-party inference API; it fronts the network's own runtime.
`/inference/v1/models` lists prices; `/inference/v1/stats` exposes revenue
and token totals for dashboards (the provider portal's demand-side panel).

## 4. Prepaid billing

Customers hold **prepaid stablecoin balances on the ledger**
(`StablecoinLedger.customer_deposit` / `customer_balance` /
`customer_debit_to_fees` / `customer_refund` — balances are liabilities
inside the same solvency invariant). The gateway resolves the customer
from the JWT subject (header fallback in dev), refuses to serve below a
configurable floor (`InferenceMarket.min_balance_to_serve_micro`,
default $0.10 — output length is unknown until the model runs, so the
floor bounds unbilled-compute exposure per request), and settles each
request with `settle_prepaid`: the metered quote is debited into the
fee split, atomically — `InsufficientBalance` moves nothing and the
request stays settleable after a top-up. Both shortfall cases surface
as HTTP 402 with the exact balance and amount due;
`GET /inference/v1/balance` is the customer's read. Production deposits
arrive as bridged stablecoins credited ledger-side.

## 5. Committed receipts — the auditable bill

Every receipt is committed to a CT-style Merkle log
(`ReceiptCommitmentLog` over `ltp.merkle_log.MerkleLog`): the receipt's
canonical, domain-separated encoding (`DOMAIN_INFERENCE_RECEIPT` in the
collision-checked registry) becomes a leaf in an append-only tree whose
heads are ML-DSA-65 signed. The gateway commits **before** settlement
and the market's verifier is the log (`receipt_verifier=log.verifier()`),
so an uncommitted or tampered receipt can never settle. The completion's
`billing.commitment` block quotes the leaf index and signed root, and
`GET /inference/v1/receipts/{request_id}` returns the full audit bundle
— record, O(log N) inclusion proof, signed tree head — which a customer
verifies without trusting the gateway: check the STH signature, walk the
audit path, recompute the SHA3-256 digests against the bodies they hold.
Anchoring the log's STHs on-chain rides the existing anchor pipeline
(`AnchorScheduler` already anchors Merkle roots), which closes the loop
to a fully on-chain-auditable bill.

## 6. Concurrency and the invariant

The solvency invariant is only as strong as the synchronization under
it. Every balance movement is a read-modify-write, and the shipped
service runs at least two threads against one ledger — the bridge
deposit poller credits while the gateway debits — so unsynchronized
movement does not merely race, it *breaks the core claim*: a deposit
landing mid-debit is silently lost, and interleaved updates can leave
the ledger holding more than was ever deposited. Both directions were
reproduced against this code before the fix. `StablecoinLedger`
therefore serializes every mutator and the invariant read behind a
reentrant lock, the check-and-debit is one atomic hold (so a balance
cannot be spent twice), and `pay_from_pool`'s clamp is evaluated under
the same hold (so concurrent payouts cannot overdraw the pool).
`ledger.lock` is exposed for callers composing multi-step atomic
sequences. Regression tests hammer all three paths and assert exact
accounting, not just "still solvent".

## 7. Running it

`ltp.inference_service.build_inference_service` composes the whole
marketplace — ledger, market, receipt log (STHs signed through the
node's ML-DSA keypair, HSM-safe per LTP-A-032), deposit watcher, epoch
settlement, gateway — into one object with `start()/stop()`;
`InferenceServiceConfig.from_env` reads `SUWAPPU_INFER_*` so it boots
identically under Docker or a shell. The model plugs in as a backend:
`openai_compatible_backend(url)` fronts the deployment's own runtime
(vLLM, TGI, llama.cpp server) and bills on the runtime's own `usage`
counts; `echo_backend()` serves dev. Bridged deposits credit through
`ltp.bridge_deposits.DepositWatcher`: idempotent per tx hash,
confirmation-gated (reorg exposure bounded by depth), and
attribution-explicit — deposits from unbound addresses are quarantined
for operations, never guessed into an account. The chain side is
`BridgeEmitterDepositSource`: it scans `BridgeEmitter.BridgeTransfer`
logs for transfers to the deposit vault over a sliding block window
(re-scans within a process lifetime are free under the watcher's
idempotency — but that idempotency set is in memory, so a **restart
re-credits every deposit still inside the lookback window**; see the
gap analysis for the durable form), and the
service polls it on a background thread (`SUWAPPU_INFER_BRIDGE_RPC_URL`
/ `_BRIDGE_EMITTER` / `_BRIDGE_DEPOSIT_RECIPIENT` activate it). With
that set, the full money path is automatic: a customer sends
stablecoins on-chain and their prepaid balance appears. The end-to-end loop —
deposit → completion over real HTTP → committed bill → audit proof
verifying → epoch payout → solvency — runs as
`examples/inference_marketplace.py` and as an integration test
(`tests/test_inference_service.py`), including under the implicit-HSM
production posture the unit-test conftest normally disables.

## 8. What this does NOT solve yet

- **Receipt-log STH anchoring config.** The commitment log publishes
  signed heads; pointing the deployment's `AnchorScheduler` at it is
  deployment wiring, not new mechanism.
- **Distributed serving.** One gateway, one backend today. Routing across
  many GPU providers (with per-provider receipts — the `node_id` field is
  already there) is the scale-out step, and per-provider attribution is
  why receipts carry `node_id` from day one.
- **The model itself.** This is the pipe and the till, not the weights.
- **Durability — the big one.** Every balance, every settled `request_id`,
  every credited tx hash, and the whole receipt log live in process
  memory. A restart resurrects spent balances and re-credits on-chain
  deposits, and the solvency invariant reports `solvent=True` throughout,
  because it checks the process against itself rather than against the
  world. Measured, not theorized.
- **Cross-process coordination.** The `RLock` in §6 is correct for one
  process and worth nothing across two: a second replica has its own
  balances and its own dedup sets, so horizontal scaling silently
  re-opens both the double-spend and the double-credit.
- **Holds.** The serve floor is an admission gate, not a reservation, so
  concurrent requests each pass it before any has debited — overrun
  scales with concurrency.
- **Receipt-log growth.** ~3,951 B/receipt retained indefinitely (~341
  GB/day at 1000 req/s). Needs a persistence tier and retention policy.

[`BILLING_LEDGER_GAP_ANALYSIS.md`](BILLING_LEDGER_GAP_ANALYSIS.md)
measures each of these against how production billing ledgers are
actually built, and gives the order to fix them in.

## 9. Reading order

1. `DEFERRED_TOKEN_ARCHITECTURE.md` — why stablecoin-native
2. `VALIDATOR_COMPUTE_INCENTIVES.md` — the supply side (proof-gated pay)
3. This document — the demand side (metered inference revenue)
4. `src/ltp/inference.py` + `tests/test_inference.py`
5. `src/ltp/gateway/routers/inference.py` + `tests/test_gateway_inference.py`
6. `src/ltp/inference_service.py` + `examples/inference_marketplace.py` —
   the composed, runnable marketplace
7. `BILLING_LEDGER_GAP_ANALYSIS.md` — what this is not yet, and why
