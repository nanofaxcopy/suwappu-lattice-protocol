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

.. warning::

   **Durability posture: this state is in-memory only.** Balances, the
   pool, per-node claims, and every dedup set live in the process. A
   restart resurrects spent balances; a second replica has its own copy
   of all of it, so the lock below serializes one process and nothing
   more. The solvency invariant is checked against this process's own
   numbers, so it reports healthy across a restart that lost money.
   Production needs an append-only double-entry journal with derived
   balances and a durable idempotency table — see
   ``docs/economics/BILLING_LEDGER_GAP_ANALYSIS.md`` for what is missing
   and in what order to fix it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .dual_lane.hashing import spec_hash_hex
from .incentives import LedgerError, StablecoinLedger

__all__ = [
    "InferenceError",
    "InferenceMarket",
    "InferencePricing",
    "InferenceReceipt",
    "InferenceSettlement",
    "InsufficientBalance",
    "MTOK",
    "ReceiptCommitmentLog",
    "receipt_digest",
    "receipt_canonical_bytes",
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
    """Protocol-frozen SHA3-256 hex digest for request/response bodies.

    Routed through ``spec_hash_hex`` rather than the canonical-lane
    helpers deliberately: a receipt digest is anchored on-chain through
    the commitment log and recomputed by customers from bodies they
    already hold, so it must stay byte-stable across profile changes.
    Following the active ``SecurityProfile`` would let a profile switch
    to SHA-384/512 invalidate every receipt issued before it.
    """
    return spec_hash_hex(payload)


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
        import threading

        # Guards the settled-request set and the revenue/token counters:
        # they are read-modify-write, and a deployment may settle from
        # more than one thread (the gateway plus operator tooling).
        self._lock = threading.Lock()
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

        with self._lock:
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
        # Let the ledger's locked check-and-debit be the authority: reading
        # the balance first and debiting second would leave a window for a
        # concurrent debit to spend it, so a shortfall is detected by the
        # debit itself and re-raised in this module's vocabulary.
        try:
            split = self.ledger.customer_debit_to_fees(customer_id, due)
        except LedgerError:
            raise InsufficientBalance(
                customer_id, self.ledger.customer_balance(customer_id), due
            ) from None
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


# ---------------------------------------------------------------------------
# Receipt commitment — the auditable billing trail
# ---------------------------------------------------------------------------


def receipt_canonical_bytes(receipt: InferenceReceipt) -> bytes:
    """Deterministic, domain-separated encoding of a receipt.

    This is the leaf committed to the Merkle log: same receipt fields,
    same bytes, always. Uses the ``DOMAIN_INFERENCE_RECEIPT`` tag from
    the collision-checked ``ltp.domain`` registry.
    """
    from .domain import DOMAIN_INFERENCE_RECEIPT
    from .encoding import CanonicalEncoder

    return (
        CanonicalEncoder(DOMAIN_INFERENCE_RECEIPT)
        .string(receipt.request_id)
        .string(receipt.node_id)
        .string(receipt.model_id)
        .uint64(receipt.input_tokens)
        .uint64(receipt.output_tokens)
        .raw_bytes(bytes.fromhex(receipt.request_digest))
        .raw_bytes(bytes.fromhex(receipt.response_digest))
        .finalize()
    )


class ReceiptCommitmentLog:
    """Commits inference receipts to a CT-style Merkle log.

    This turns the billing trail into evidence: every served request's
    receipt (digests + metering, never the payloads) becomes a leaf in
    an append-only BLAKE2b-256 Merkle tree whose heads are signed with
    the operator's ML-DSA-65 key. A customer holding the response's
    ``billing`` block can fetch the inclusion proof and verify — against
    a post-quantum signed tree head — that they were billed for exactly
    the exchange whose digests they can recompute locally.

    Wiring: the gateway calls :meth:`commit` after building the receipt
    and before settlement, and the market is constructed with
    ``receipt_verifier=log.verifier()`` so an uncommitted (or tampered)
    receipt can never settle. One STH is published per commit — receipts
    are low-volume relative to shards, and per-commit heads give every
    bill an immediately quotable anchor.
    """

    def __init__(self, merkle_log: Any) -> None:
        """``merkle_log`` is a ``ltp.merkle_log.MerkleLog`` (duck-typed
        to avoid importing the log stack at module import time)."""
        self._log = merkle_log
        self._index_by_request: dict[str, int] = {}

    # --- Committing ---

    def commit(self, receipt: InferenceReceipt) -> int:
        """Append the receipt's canonical leaf and publish an STH.

        Returns the leaf index. Committing the same ``request_id`` twice
        is rejected — one request, one leaf.
        """
        if receipt.request_id in self._index_by_request:
            raise InferenceError(f"receipt already committed: {receipt.request_id}")
        index = self._log.append(receipt_canonical_bytes(receipt))
        self._log.publish_sth()
        self._index_by_request[receipt.request_id] = index
        return index

    def leaf_index(self, request_id: str) -> int | None:
        """The committed leaf index for ``request_id``, if any."""
        return self._index_by_request.get(request_id)

    @property
    def latest_sth(self) -> Any:
        """The most recently published signed tree head."""
        return self._log.latest_sth

    # --- Verifying ---

    def is_committed(self, receipt: InferenceReceipt) -> bool:
        """True iff this exact receipt (byte-identical canonical leaf)
        is committed in the log under its ``request_id``."""
        index = self._index_by_request.get(receipt.request_id)
        if index is None:
            return False
        return self._log.get_record(index) == receipt_canonical_bytes(receipt)

    def verifier(self) -> Callable[[InferenceReceipt], bool]:
        """A receipt verifier for ``InferenceMarket(receipt_verifier=...)``.

        With this wired, settlement refuses any receipt that was not
        committed — or whose fields differ from what was committed.
        """
        return self.is_committed

    # --- Auditing ---

    def proof(self, request_id: str) -> dict[str, Any]:
        """A self-contained, JSON-serializable audit bundle for one bill.

        Contains the canonical record, the O(log N) inclusion proof, and
        the latest ML-DSA-signed tree head. A customer verifies by (a)
        checking the STH signature, (b) recomputing the record's leaf
        against the audit path up to the STH root, and (c) recomputing
        the request/response digests inside the record against the
        bodies they hold. Raises ``InferenceError`` for an unknown
        ``request_id``.
        """
        index = self._index_by_request.get(request_id)
        if index is None:
            raise InferenceError(f"no committed receipt for request: {request_id}")
        record = self._log.get_record(index)
        inclusion = self._log.inclusion_proof(index)
        sth = self._log.latest_sth
        return {
            "request_id": request_id,
            "leaf_index": inclusion.leaf_index,
            "tree_size": inclusion.tree_size,
            "record": record.hex(),
            "audit_path": [node.hex() for node in inclusion.audit_path],
            "root_hash": inclusion.root_hash.hex(),
            "sth": {
                "sequence": sth.sequence,
                "tree_size": sth.tree_size,
                "timestamp": sth.timestamp,
                "root_hash": sth.root_hash.hex(),
                "operator_vk": sth.operator_vk.hex(),
                "signature": sth.signature.hex(),
            },
        }
