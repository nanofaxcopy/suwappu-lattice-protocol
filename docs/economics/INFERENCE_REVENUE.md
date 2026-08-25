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
3. **Receipts are evidence.** SHA3-256 digests (the canonical/on-chain hash
   lane) of the exact request and response bodies ride the receipt; the
   bodies never enter the billing path. The receipt verifier is injected —
   production wires one that checks digests against an LTP commitment, so a
   customer can audit "you billed me for exactly this exchange" against the
   anchored log without anyone shipping the payloads. That is the LTP moat:
   **no other inference market can hand its customers a post-quantum,
   on-chain-anchored billing trail.**
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

## 6. Running it

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
(re-scans are free under the watcher's idempotency, so restarts and
RPC hiccups can neither double-credit nor silently skip), and the
service polls it on a background thread (`SUWAPPU_INFER_BRIDGE_RPC_URL`
/ `_BRIDGE_EMITTER` / `_BRIDGE_DEPOSIT_RECIPIENT` activate it). With
that set, the full money path is automatic: a customer sends
stablecoins on-chain and their prepaid balance appears. The end-to-end loop —
deposit → completion over real HTTP → committed bill → audit proof
verifying → epoch payout → solvency — runs as
`examples/inference_marketplace.py` and as an integration test
(`tests/test_inference_service.py`), including under the implicit-HSM
production posture the unit-test conftest normally disables.

## 7. What this does NOT solve yet

- **Receipt-log STH anchoring config.** The commitment log publishes
  signed heads; pointing the deployment's `AnchorScheduler` at it is
  deployment wiring, not new mechanism.
- **Distributed serving.** One gateway, one backend today. Routing across
  many GPU providers (with per-provider receipts — the `node_id` field is
  already there) is the scale-out step, and per-provider attribution is
  why receipts carry `node_id` from day one.
- **The model itself.** This is the pipe and the till, not the weights.

## 8. Reading order

1. `DEFERRED_TOKEN_ARCHITECTURE.md` — why stablecoin-native
2. `VALIDATOR_COMPUTE_INCENTIVES.md` — the supply side (proof-gated pay)
3. This document — the demand side (metered inference revenue)
4. `src/ltp/inference.py` + `tests/test_inference.py`
5. `src/ltp/gateway/routers/inference.py` + `tests/test_gateway_inference.py`
6. `src/ltp/inference_service.py` + `examples/inference_marketplace.py` —
   the composed, runnable marketplace
