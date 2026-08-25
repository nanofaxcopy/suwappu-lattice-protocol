"""
Inference market — sell model inference on the provider network, settle
in stablecoins, and fund the compute-incentive loop with the revenue.

This is the demand side that ``ltp.incentives`` deliberately left open:
that module enforces how proven work gets *paid* (solvency-clamped,
proof-gated); this one is where the money enters. A customer buys
inference against the network's model, pays a metered stablecoin fee,
and the fee flows through the same ``StablecoinLedger`` split — the
serving provider's share accrues as a claim on the operator pool, the
insurance and treasury shares fund the same pools that back the
validator economy. One ledger, one operator identity, one solvency
invariant.

Billing rules (the load-bearing ones):

  1. **Metered, not subscription.** Price is per token, quoted up front
     (``InferencePricing.quote``) from per-million-token input/output
     rates. What was quoted is what settles; underpayment is rejected,
     overpayment is accepted verbatim (tips are revenue).

  2. **One receipt, one settlement.** Every settlement is keyed by the
     receipt's ``request_id``; a replayed receipt is rejected. This is
     the same duplicate-redemption posture as the issuer precompile's
     two-phase burn on the chain side.

  3. **Receipts are evidence, not claims.** An ``InferenceReceipt``
     carries SHA3-256 digests of the exact request and response bodies.
     Deployments verify receipts before settling — the verifier is
     injected (default: structural validation only) so the production
     path can require an LTP commitment of the digests (the request/
     response committed through ``LTPProtocol.commit`` and anchored),
     making the billing trail independently auditable without shipping
     the payloads themselves.

  4. **Revenue is split, never held.** ``settle`` immediately deposits
     the fee into the ledger (operator/insurance/treasury split from
     ``IncentiveConfig``) and accrues the serving node's claim equal to
     the operator share of its own revenue. Payout still happens at
     epoch settlement under the pool's solvency clamp — inference
     revenue and storage fees share one pool by design (one operator
     economy, per the deferred-token architecture).

The gateway surface for this module is
``ltp.gateway.routers.inference`` — an OpenAI-style completions
endpoint that runs the configured model backend, meters usage, and
settles here. See ``docs/economics/INFERENCE_REVENUE.md``.

Like ``ltp.incentives``, this module is intentionally NOT re-exported
from ``ltp.__init__`` yet (private per ``docs/STABILITY_PROMISES.md``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .incentives import StablecoinLedger

__all__ = [
    "InferenceError",
    "InferenceMarket",
    "InferencePricing",
    "InferenceReceipt",
    "InferenceSettlement",
    "InsufficientBalance",
    "MTOK",
    "receipt_digest",
]

MTOK = 1_000_000  # tokens per "per-million-token" price unit

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class InferenceError(Exception):
    """Raised on billing violations: bad receipts, replays, underpayment."""


class InsufficientBalance(InferenceError):
    """A prepaid settlement was refused: the customer's balance is short.

    Carries ``balance_micro`` and ``due_micro`` so callers (the gateway's
    402 path) can tell the customer exactly what to top up.
    """

    def __init__(self, customer_id: str, balance_micro: int, due_micro: int) -> None:
        super().__init__(f"customer {customer_id} balance {balance_micro} cannot cover {due_micro}")
        self.customer_id = customer_id
        self.balance_micro = balance_micro
        self.due_micro = due_micro


def receipt_digest(payload: bytes) -> str:
    """Canonical SHA3-256 hex digest for request/response bodies.

    SHA3-256 is the canonical/on-chain lane of the dual-lane hashing
    architecture, which is what a billing artifact that may later be
    committed on-chain must use.
    """
    return hashlib.sha3_256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


@dataclass
class InferencePricing:
    """Per-model metered pricing, in stablecoin micro-units.

    Rates are quoted per million tokens (the industry-standard unit) so
    a listing reads like any commercial model card.
    """

    model_id: str
    input_micro_per_mtok: int
    output_micro_per_mtok: int

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("model_id must be non-empty")
        if self.input_micro_per_mtok < 0 or self.output_micro_per_mtok < 0:
            raise ValueError("prices must be non-negative")

    def quote(self, input_tokens: int, output_tokens: int) -> int:
        """Price a request in micro. Rounds up — partial tokens bill whole.

        Ceiling division keeps the quote monotone and ensures a nonzero
        request against a nonzero rate never prices at zero.
        """
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        cost_in = -(-input_tokens * self.input_micro_per_mtok // MTOK)
        cost_out = -(-output_tokens * self.output_micro_per_mtok // MTOK)
        return cost_in + cost_out


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


@dataclass
class InferenceReceipt:
    """One served request: who served what, metered how, evidenced by what.

    ``request_digest`` / ``response_digest`` are SHA3-256 hex over the
    exact request and response bodies (``receipt_digest``). The bodies
    themselves never enter the billing path.
    """

    request_id: str
    node_id: str
    model_id: str
    input_tokens: int
    output_tokens: int
    request_digest: str
    response_digest: str

    def __post_init__(self) -> None:
        if not self.request_id or not self.node_id or not self.model_id:
            raise ValueError("request_id, node_id, and model_id must be non-empty")
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        for digest in (self.request_digest, self.response_digest):
            if not _HEX64.match(digest):
                raise ValueError("digests must be 64-char lowercase hex (SHA3-256)")


@dataclass
class InferenceSettlement:
    """Outcome of settling one receipt."""

    request_id: str
    node_id: str
    model_id: str
    revenue_micro: int
    provider_claim_micro: int
    insurance_micro: int
    treasury_micro: int
    # Set on prepaid settlements: who was debited, and what remains.
    customer_id: str | None = None
    customer_balance_after_micro: int | None = None


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------


class InferenceMarket:
    """Meters inference revenue into the stablecoin ledger.

    ``receipt_verifier`` is injected like ``StableAdmissionControl``'s
    storage-proof verifier: it receives the receipt and must return
    truthy for settlement to proceed. The default accepts any
    structurally valid receipt; production wires a verifier that checks
    the digests against an LTP commitment.
    """

    def __init__(
        self,
        ledger: StablecoinLedger,
        receipt_verifier: Callable[[InferenceReceipt], Any] | None = None,
        min_balance_to_serve_micro: int = 100_000,  # $0.10
    ) -> None:
        if min_balance_to_serve_micro < 0:
            raise ValueError("min_balance_to_serve_micro must be non-negative")
        self.ledger = ledger
        self._verify_receipt = receipt_verifier or (lambda receipt: True)
        # Floor a customer must hold before a request is served at all.
        # Output length is unknown until the model runs, so serving a
        # broke customer risks unbilled compute; the floor bounds that
        # exposure per request. The gateway checks it pre-serve.
        self.min_balance_to_serve_micro = min_balance_to_serve_micro
        self._pricing: dict[str, InferencePricing] = {}
        self._settled_requests: set[str] = set()
        self._revenue_by_model: dict[str, int] = {}
        self._tokens_by_model: dict[str, int] = {}

    # --- Listings ---

    def register_model(self, pricing: InferencePricing) -> None:
        """List (or re-price) a model. Re-pricing applies to new quotes only."""
        self._pricing[pricing.model_id] = pricing

    def models(self) -> list[InferencePricing]:
        """Current listings, in registration order."""
        return list(self._pricing.values())

    def pricing_for(self, model_id: str) -> InferencePricing:
        """Pricing for ``model_id``; raises ``InferenceError`` if unlisted."""
        if model_id not in self._pricing:
            raise InferenceError(f"model not listed: {model_id}")
        return self._pricing[model_id]

    def quote(self, model_id: str, input_tokens: int, output_tokens: int) -> int:
        """Quote a request in micro against the model's current listing."""
        return self.pricing_for(model_id).quote(input_tokens, output_tokens)

    # --- Settlement ---

    def _validate_for_settlement(self, receipt: InferenceReceipt) -> int:
        """Shared pre-settlement checks. Returns the metered quote (micro)."""
        pricing = self.pricing_for(receipt.model_id)
        if receipt.request_id in self._settled_requests:
            raise InferenceError(f"request already settled: {receipt.request_id}")
        if not self._verify_receipt(receipt):
            raise InferenceError(f"receipt verification failed: {receipt.request_id}")
        return pricing.quote(receipt.input_tokens, receipt.output_tokens)

    def _record_settlement(
        self,
        receipt: InferenceReceipt,
        revenue_micro: int,
        split: tuple[int, int, int],
        customer_id: str | None = None,
    ) -> InferenceSettlement:
        """Accrue the provider claim and record totals. Split is already applied."""
        operator, insurance, treasury = split
        # The serving node's claim is the operator share of its own
        # revenue; payment still flows through epoch settlement under
        # the pool's solvency clamp.
        account = self.ledger.account(receipt.node_id)
        if not account.evicted:
            account.earned_claim_micro += operator

        self._settled_requests.add(receipt.request_id)
        self._revenue_by_model[receipt.model_id] = (
            self._revenue_by_model.get(receipt.model_id, 0) + revenue_micro
        )
        self._tokens_by_model[receipt.model_id] = (
            self._tokens_by_model.get(receipt.model_id, 0)
            + receipt.input_tokens
            + receipt.output_tokens
        )
        return InferenceSettlement(
            request_id=receipt.request_id,
            node_id=receipt.node_id,
            model_id=receipt.model_id,
            revenue_micro=revenue_micro,
            provider_claim_micro=operator if not account.evicted else 0,
            insurance_micro=insurance,
            treasury_micro=treasury,
            customer_id=customer_id,
            customer_balance_after_micro=(
                self.ledger.customer_balance(customer_id) if customer_id else None
            ),
        )

    def settle(self, receipt: InferenceReceipt, paid_micro: int) -> InferenceSettlement:
        """Settle one served request paid out-of-band: verify, bill, split.

        For payment collected outside the ledger (an invoice, a bridge
        transfer referencing this request). Raises ``InferenceError`` on
        an unlisted model, a replayed ``request_id``, a failed receipt
        verification, or payment below the metered quote. On any raise
        nothing is recorded — the request stays settleable once the
        defect is fixed.
        """
        due = self._validate_for_settlement(receipt)
        if paid_micro < due:
            raise InferenceError(f"underpayment: paid {paid_micro}, metered {due}")
        split = self.ledger.deposit_fee(paid_micro)
        return self._record_settlement(receipt, paid_micro, split)

    def settle_prepaid(self, receipt: InferenceReceipt, customer_id: str) -> InferenceSettlement:
        """Settle one served request against a customer's prepaid balance.

        The metered quote is debited from the customer's ledger balance
        into the fee split. Raises ``InsufficientBalance`` (with the
        balance and the amount due) when the balance can't cover it —
        nothing moves, and the request stays settleable after a top-up.
        """
        if not customer_id:
            raise InferenceError("customer_id must be non-empty")
        due = self._validate_for_settlement(receipt)
        balance = self.ledger.customer_balance(customer_id)
        if balance < due:
            raise InsufficientBalance(customer_id, balance, due)
        split = self.ledger.customer_debit_to_fees(customer_id, due)
        return self._record_settlement(receipt, due, split, customer_id=customer_id)

    # --- Introspection ---

    @property
    def settled_count(self) -> int:
        """Number of settled requests."""
        return len(self._settled_requests)

    def revenue_micro(self, model_id: str | None = None) -> int:
        """Total settled revenue, for one model or across all."""
        if model_id is not None:
            return self._revenue_by_model.get(model_id, 0)
        return sum(self._revenue_by_model.values())

    def tokens_served(self, model_id: str | None = None) -> int:
        """Total metered tokens, for one model or across all."""
        if model_id is not None:
            return self._tokens_by_model.get(model_id, 0)
        return sum(self._tokens_by_model.values())
