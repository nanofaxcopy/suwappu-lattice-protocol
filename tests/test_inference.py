"""
Tests for src/ltp/inference.py — the inference market billing engine.

Covers:
  - InferencePricing validation and per-million-token quoting (ceiling)
  - InferenceReceipt structural validation (digest shape, token counts)
  - Model listing / re-pricing / unlisted-model rejection
  - settle(): revenue split into the ledger, provider claim accrual,
    solvency preserved end to end
  - Replay rejection (one receipt, one settlement)
  - Underpayment rejection; overpayment accepted as revenue
  - Injected receipt verifier gating settlement
  - Evicted serving node: customer billed, no claim accrued
  - Revenue funding the incentive loop: inference fee in, epoch
    settlement pays the serving provider from the operator pool
  - Prepaid customer accounts: deposit/balance/debit/refund on the
    ledger with solvency intact; settle_prepaid debiting the quote,
    InsufficientBalance leaving everything untouched and retryable
  - Introspection totals (settled_count, revenue, tokens)
"""

import pytest

from src.ltp.incentives import IncentiveConfig, StablecoinLedger, StableNodeIncentive
from src.ltp.inference import (
    MTOK,
    InferenceError,
    InferenceMarket,
    InferencePricing,
    InferenceReceipt,
    InsufficientBalance,
    receipt_digest,
)

PRICING = InferencePricing(
    model_id="suwappu-1",
    input_micro_per_mtok=250_000,  # $0.25 / MTok in
    output_micro_per_mtok=1_000_000,  # $1.00 / MTok out
)


def make_market(**market_kwargs) -> InferenceMarket:
    market = InferenceMarket(StablecoinLedger(IncentiveConfig()), **market_kwargs)
    market.register_model(PRICING)
    return market


def make_receipt(request_id="req-1", node_id="gpu-1", **overrides) -> InferenceReceipt:
    fields = dict(
        request_id=request_id,
        node_id=node_id,
        model_id="suwappu-1",
        input_tokens=1_000,
        output_tokens=500,
        request_digest=receipt_digest(b"request"),
        response_digest=receipt_digest(b"response"),
    )
    fields.update(overrides)
    return InferenceReceipt(**fields)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


class TestPricing:
    def test_quote_per_million_tokens(self):
        assert PRICING.quote(MTOK, MTOK) == 250_000 + 1_000_000

    def test_quote_rounds_up(self):
        # 1 input token at $0.25/MTok is a fraction of a micro — bills 1.
        assert PRICING.quote(1, 0) == 1

    def test_zero_tokens_zero_quote(self):
        assert PRICING.quote(0, 0) == 0

    def test_quote_monotone(self):
        assert PRICING.quote(2_000, 500) >= PRICING.quote(1_000, 500)

    def test_negative_tokens_rejected(self):
        with pytest.raises(ValueError):
            PRICING.quote(-1, 0)

    def test_bad_pricing_rejected(self):
        with pytest.raises(ValueError):
            InferencePricing(model_id="", input_micro_per_mtok=1, output_micro_per_mtok=1)
        with pytest.raises(ValueError):
            InferencePricing(model_id="m", input_micro_per_mtok=-1, output_micro_per_mtok=1)


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


class TestReceipts:
    def test_valid_receipt(self):
        make_receipt()  # must not raise

    def test_bad_digest_rejected(self):
        with pytest.raises(ValueError):
            make_receipt(request_digest="nothex")
        with pytest.raises(ValueError):
            make_receipt(response_digest="AB" * 32)  # uppercase

    def test_negative_tokens_rejected(self):
        with pytest.raises(ValueError):
            make_receipt(input_tokens=-1)

    def test_empty_ids_rejected(self):
        with pytest.raises(ValueError):
            make_receipt(request_id="")


# ---------------------------------------------------------------------------
# Market settlement
# ---------------------------------------------------------------------------


class TestSettlement:
    def test_settle_splits_revenue_and_accrues_claim(self):
        market = make_market()
        ledger = market.ledger
        receipt = make_receipt()
        due = market.quote("suwappu-1", 1_000, 500)
        settlement = market.settle(receipt, due)

        cfg = ledger.config
        expected_operator = (
            due
            - (due * cfg.fee_insurance_share_bps // 10_000)
            - (due * cfg.fee_treasury_share_bps // 10_000)
        )
        assert settlement.revenue_micro == due
        assert settlement.provider_claim_micro == expected_operator
        assert ledger.account("gpu-1").earned_claim_micro == expected_operator
        assert ledger.operator_pool_micro == expected_operator
        assert ledger.check_solvency()

    def test_replay_rejected(self):
        market = make_market()
        due = market.quote("suwappu-1", 1_000, 500)
        market.settle(make_receipt(), due)
        with pytest.raises(InferenceError):
            market.settle(make_receipt(), due)
        assert market.settled_count == 1

    def test_unlisted_model_rejected(self):
        market = make_market()
        with pytest.raises(InferenceError):
            market.settle(make_receipt(model_id="other"), 10_000)

    def test_underpayment_rejected_and_nothing_recorded(self):
        market = make_market()
        due = market.quote("suwappu-1", 1_000, 500)
        with pytest.raises(InferenceError):
            market.settle(make_receipt(), due - 1)
        assert market.settled_count == 0
        assert market.ledger.total_held_micro == 0
        # The same request settles once paid in full.
        market.settle(make_receipt(), due)

    def test_overpayment_accepted_as_revenue(self):
        market = make_market()
        due = market.quote("suwappu-1", 1_000, 500)
        settlement = market.settle(make_receipt(), due + 999)
        assert settlement.revenue_micro == due + 999
        assert market.ledger.check_solvency()

    def test_receipt_verifier_gates_settlement(self):
        market = make_market(receipt_verifier=lambda receipt: False)
        with pytest.raises(InferenceError):
            market.settle(make_receipt(), 10_000)
        assert market.settled_count == 0

    def test_evicted_node_earns_no_claim_but_customer_is_billed(self):
        market = make_market()
        market.ledger.account("gpu-1").evicted = True
        due = market.quote("suwappu-1", 1_000, 500)
        settlement = market.settle(make_receipt(), due)
        assert settlement.provider_claim_micro == 0
        assert market.ledger.account("gpu-1").earned_claim_micro == 0
        # Revenue still entered the ledger and stays solvent.
        assert market.ledger.total_held_micro == due
        assert market.ledger.check_solvency()


# ---------------------------------------------------------------------------
# The revenue loop
# ---------------------------------------------------------------------------


class TestRevenueLoop:
    def test_inference_revenue_pays_provider_at_epoch_settlement(self):
        """Customer fee in -> operator pool -> epoch payout to the server."""
        market = make_market()
        ledger = market.ledger
        incentive = StableNodeIncentive(ledger)
        due = market.quote("suwappu-1", 100_000, 50_000)
        settlement = market.settle(make_receipt(), due)

        snapshot = incentive.settle_epoch(1)
        assert snapshot.payouts["gpu-1"] == settlement.provider_claim_micro
        assert snapshot.fully_funded
        assert ledger.account("gpu-1").paid_total_micro == settlement.provider_claim_micro
        assert ledger.check_solvency()

    def test_totals(self):
        market = make_market()
        due = market.quote("suwappu-1", 1_000, 500)
        market.settle(make_receipt("r1"), due)
        market.settle(make_receipt("r2"), due)
        assert market.settled_count == 2
        assert market.revenue_micro() == 2 * due
        assert market.revenue_micro("suwappu-1") == 2 * due
        assert market.tokens_served() == 2 * 1_500
        assert market.revenue_micro("missing") == 0


# ---------------------------------------------------------------------------
# Prepaid customer accounts
# ---------------------------------------------------------------------------


class TestCustomerAccounts:
    def test_deposit_and_balance(self):
        market = make_market()
        ledger = market.ledger
        assert ledger.customer_deposit("alice", 5_000) == 5_000
        assert ledger.customer_deposit("alice", 1_000) == 6_000
        assert ledger.customer_balance("alice") == 6_000
        assert ledger.customer_balance("unknown") == 0
        assert ledger.check_solvency()

    def test_debit_moves_balance_into_fee_split(self):
        market = make_market()
        ledger = market.ledger
        ledger.customer_deposit("alice", 10_000)
        operator, insurance, treasury = ledger.customer_debit_to_fees("alice", 10_000)
        assert operator + insurance + treasury == 10_000
        assert ledger.customer_balance("alice") == 0
        assert ledger.operator_pool_micro == operator
        assert ledger.check_solvency()

    def test_debit_beyond_balance_rejected_atomically(self):
        market = make_market()
        ledger = market.ledger
        ledger.customer_deposit("alice", 100)
        with pytest.raises(Exception):
            ledger.customer_debit_to_fees("alice", 101)
        assert ledger.customer_balance("alice") == 100
        assert ledger.operator_pool_micro == 0
        assert ledger.check_solvency()

    def test_refund_returns_remaining_balance(self):
        market = make_market()
        ledger = market.ledger
        ledger.customer_deposit("alice", 700)
        assert ledger.customer_refund("alice") == 700
        assert ledger.customer_balance("alice") == 0
        assert ledger.check_solvency()


class TestPrepaidSettlement:
    def test_settle_prepaid_debits_quote(self):
        market = make_market()
        ledger = market.ledger
        due = market.quote("suwappu-1", 1_000, 500)
        ledger.customer_deposit("alice", due + 123)
        settlement = market.settle_prepaid(make_receipt(), "alice")
        assert settlement.revenue_micro == due
        assert settlement.customer_id == "alice"
        assert settlement.customer_balance_after_micro == 123
        assert ledger.customer_balance("alice") == 123
        assert ledger.account("gpu-1").earned_claim_micro == settlement.provider_claim_micro
        assert ledger.check_solvency()

    def test_insufficient_balance_touches_nothing_and_stays_retryable(self):
        market = make_market()
        ledger = market.ledger
        due = market.quote("suwappu-1", 1_000, 500)
        ledger.customer_deposit("alice", due - 1)
        with pytest.raises(InsufficientBalance) as excinfo:
            market.settle_prepaid(make_receipt(), "alice")
        assert excinfo.value.balance_micro == due - 1
        assert excinfo.value.due_micro == due
        assert market.settled_count == 0
        assert ledger.customer_balance("alice") == due - 1
        # A top-up makes the same request settleable.
        ledger.customer_deposit("alice", 1)
        market.settle_prepaid(make_receipt(), "alice")
        assert ledger.check_solvency()

    def test_prepaid_replay_rejected_before_debit(self):
        market = make_market()
        ledger = market.ledger
        due = market.quote("suwappu-1", 1_000, 500)
        ledger.customer_deposit("alice", 2 * due)
        market.settle_prepaid(make_receipt(), "alice")
        with pytest.raises(InferenceError):
            market.settle_prepaid(make_receipt(), "alice")
        # Only one debit happened.
        assert ledger.customer_balance("alice") == due
        assert ledger.check_solvency()

    def test_empty_customer_rejected(self):
        market = make_market()
        with pytest.raises(InferenceError):
            market.settle_prepaid(make_receipt(), "")

    def test_prepaid_revenue_pays_provider_at_epoch_settlement(self):
        from src.ltp.incentives import StableNodeIncentive

        market = make_market()
        ledger = market.ledger
        due = market.quote("suwappu-1", 100_000, 50_000)
        ledger.customer_deposit("alice", due)
        settlement = market.settle_prepaid(make_receipt(), "alice")
        snapshot = StableNodeIncentive(ledger).settle_epoch(1)
        assert snapshot.payouts["gpu-1"] == settlement.provider_claim_micro
        assert ledger.check_solvency()
