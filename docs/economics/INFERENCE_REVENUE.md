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

## 4. What this does NOT solve yet

- **Prepaid balances / payment rails.** `settle` takes `paid_micro` as an
  argument; the production path needs customer deposit accounts on the
  ledger (stablecoin in via the bridge) debited per request. Additive
  change to `StablecoinLedger`; scoped separately.
- **Receipt commitment wiring.** The verifier hook exists; the default is
  structural. Wiring `LTPProtocol.commit` for request/response digests +
  anchoring is the hardening step that makes billing independently
  auditable.
- **Distributed serving.** One gateway, one backend today. Routing across
  many GPU providers (with per-provider receipts — the `node_id` field is
  already there) is the scale-out step, and per-provider attribution is
  why receipts carry `node_id` from day one.
- **The model itself.** This is the pipe and the till, not the weights.

## 5. Reading order

1. `DEFERRED_TOKEN_ARCHITECTURE.md` — why stablecoin-native
2. `VALIDATOR_COMPUTE_INCENTIVES.md` — the supply side (proof-gated pay)
3. This document — the demand side (metered inference revenue)
4. `src/ltp/inference.py` + `tests/test_inference.py`
5. `src/ltp/gateway/routers/inference.py` + `tests/test_gateway_inference.py`
