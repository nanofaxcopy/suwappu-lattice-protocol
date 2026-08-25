"""
Tests for src/ltp/incentives.py — stablecoin-denominated compute incentives.

Covers:
  - IncentiveConfig validation (fee split must sum to 10000 bps)
  - Metering rates (storage byte-seconds, serving bytes)
  - StablecoinLedger solvency invariant across every flow
  - Fee deposit splitting (operator / insurance / treasury, no burn)
  - Funded incentive budget (no minting)
  - §5.5 interface conformance (runtime_checkable Protocols)
  - compensate() accrual and evicted-node zeroing
  - Proof-gated accrual: zero audits ⇒ zero pay; pass-ratio scaling
  - Slashing tiers against the stablecoin bond, routed to insurance
  - Epoch settlement: full pay when funded, pro-rata when short,
    carried claims never paid from unheld balance
  - StableCommitmentPricing price/renew and fee collection loop
  - StableAdmissionControl bond floor, proof verification, eviction
    bond forfeiture vs refund
"""

import pytest

from src.ltp.incentives import (
    GIB,
    MICRO_PER_STABLE,
    SECONDS_PER_MONTH,
    AdmissionControl,
    AdmissionDecision,
    CommitmentPricing,
    IncentiveConfig,
    MeteredWorkReport,
    NodeIncentive,
    StableAdmissionControl,
    StablecoinLedger,
    StableCommitmentPricing,
    StableNodeIncentive,
)


def make_ledger(**config_overrides) -> StablecoinLedger:
    return StablecoinLedger(IncentiveConfig(**config_overrides))


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestIncentiveConfig:
    def test_default_fee_split_sums_to_10000(self):
        IncentiveConfig()  # must not raise

    def test_bad_fee_split_rejected(self):
        with pytest.raises(ValueError):
            IncentiveConfig(fee_operator_share_bps=9_000)

    def test_negative_bond_rejected(self):
        with pytest.raises(ValueError):
            IncentiveConfig(min_bond_micro=-1)

    def test_storage_rate_one_gib_month(self):
        cfg = IncentiveConfig()
        micro = cfg.storage_rate_micro(GIB * SECONDS_PER_MONTH)
        assert micro == cfg.price_per_gib_month_micro

    def test_serving_rate_one_gib(self):
        cfg = IncentiveConfig()
        assert cfg.serving_rate_micro(GIB) == cfg.price_per_gib_served_micro


# ---------------------------------------------------------------------------
# Ledger solvency
# ---------------------------------------------------------------------------


class TestLedgerSolvency:
    def test_fee_deposit_split_conserves_units(self):
        ledger = make_ledger()
        operator, insurance, treasury = ledger.deposit_fee(10_001)
        assert operator + insurance + treasury == 10_001
        assert ledger.check_solvency()

    def test_fee_split_shares(self):
        ledger = make_ledger()
        operator, insurance, treasury = ledger.deposit_fee(10_000)
        assert operator == 8_000
        assert insurance == 1_000
        assert treasury == 1_000

    def test_budget_funding_goes_to_operator_pool(self):
        ledger = make_ledger()
        ledger.fund_incentive_budget(5_000)
        assert ledger.operator_pool_micro == 5_000
        assert ledger.check_solvency()

    def test_pool_payment_clamped_to_balance(self):
        ledger = make_ledger()
        ledger.fund_incentive_budget(100)
        paid = ledger.pay_from_pool("n1", 250)
        assert paid == 100
        assert ledger.operator_pool_micro == 0
        assert ledger.check_solvency()

    def test_slash_moves_bond_to_insurance(self):
        ledger = make_ledger()
        ledger.post_bond("n1", 1_000)
        slashed = ledger.slash_bond("n1", 300)
        assert slashed == 300
        assert ledger.account("n1").bond_micro == 700
        assert ledger.insurance_pool_micro == 300
        assert ledger.check_solvency()

    def test_slash_clamped_to_bond(self):
        ledger = make_ledger()
        ledger.post_bond("n1", 100)
        assert ledger.slash_bond("n1", 500) == 100
        assert ledger.check_solvency()

    def test_refund_and_forfeit(self):
        ledger = make_ledger()
        ledger.post_bond("n1", 400)
        ledger.post_bond("n2", 600)
        assert ledger.refund_bond("n1") == 400
        assert ledger.forfeit_bond_to_insurance("n2") == 600
        assert ledger.insurance_pool_micro == 600
        assert ledger.check_solvency()

    def test_negative_amounts_rejected(self):
        ledger = make_ledger()
        for op in (
            lambda: ledger.deposit_fee(-1),
            lambda: ledger.fund_incentive_budget(-1),
            lambda: ledger.post_bond("n1", -1),
            lambda: ledger.pay_from_pool("n1", -1),
            lambda: ledger.slash_bond("n1", -1),
        ):
            with pytest.raises(Exception):
                op()


# ---------------------------------------------------------------------------
# §5.5 interface conformance
# ---------------------------------------------------------------------------


class TestInterfaceConformance:
    def test_implementations_satisfy_protocols(self):
        ledger = make_ledger()
        assert isinstance(StableNodeIncentive(ledger), NodeIncentive)
        assert isinstance(StableCommitmentPricing(ledger), CommitmentPricing)
        assert isinstance(StableAdmissionControl(ledger), AdmissionControl)


# ---------------------------------------------------------------------------
# NodeIncentive
# ---------------------------------------------------------------------------


class TestCompensation:
    def test_compensate_accrues_metered_claim(self):
        ledger = make_ledger()
        incentive = StableNodeIncentive(ledger)
        reward = incentive.compensate(
            "n1", bytes_stored=GIB, seconds_stored=SECONDS_PER_MONTH, bytes_served=GIB
        )
        cfg = ledger.config
        assert reward == cfg.price_per_gib_month_micro + cfg.price_per_gib_served_micro
        assert ledger.account("n1").earned_claim_micro == reward

    def test_compensate_evicted_node_earns_zero(self):
        ledger = make_ledger()
        ledger.account("n1").evicted = True
        incentive = StableNodeIncentive(ledger)
        assert incentive.compensate("n1", GIB, SECONDS_PER_MONTH, 0) == 0

    def test_compensate_rejects_negative_inputs(self):
        incentive = StableNodeIncentive(make_ledger())
        with pytest.raises(ValueError):
            incentive.compensate("n1", -1, 10, 0)


class TestProofGatedAccrual:
    def test_zero_audits_earn_zero(self):
        incentive = StableNodeIncentive(make_ledger())
        report = MeteredWorkReport(
            node_id="n1",
            epoch=1,
            bytes_stored=GIB,
            seconds_stored=SECONDS_PER_MONTH,
            audits_passed=0,
            audits_total=0,
        )
        assert incentive.accrue_report(report) == 0

    def test_pass_ratio_scales_pay(self):
        ledger = make_ledger()
        incentive = StableNodeIncentive(ledger)
        full = MeteredWorkReport(
            node_id="full",
            epoch=1,
            bytes_stored=GIB,
            seconds_stored=SECONDS_PER_MONTH,
            audits_passed=4,
            audits_total=4,
        )
        half = MeteredWorkReport(
            node_id="half",
            epoch=1,
            bytes_stored=GIB,
            seconds_stored=SECONDS_PER_MONTH,
            audits_passed=2,
            audits_total=4,
        )
        full_pay = incentive.accrue_report(full)
        half_pay = incentive.accrue_report(half)
        assert full_pay == ledger.config.price_per_gib_month_micro
        assert half_pay == full_pay // 2

    def test_failures_beyond_threshold_slash_bond(self):
        ledger = make_ledger()
        ledger.post_bond("n1", 1_000 * MICRO_PER_STABLE)
        incentive = StableNodeIncentive(ledger)
        report = MeteredWorkReport(node_id="n1", epoch=1, audits_passed=1, audits_total=4)
        incentive.accrue_report(report)
        acct = ledger.account("n1")
        assert acct.slashed_total_micro > 0
        assert ledger.insurance_pool_micro == acct.slashed_total_micro
        assert ledger.check_solvency()

    def test_single_failure_discounts_without_slash(self):
        ledger = make_ledger()
        ledger.post_bond("n1", 1_000 * MICRO_PER_STABLE)
        incentive = StableNodeIncentive(ledger)
        report = MeteredWorkReport(node_id="n1", epoch=1, audits_passed=3, audits_total=4)
        incentive.accrue_report(report)
        assert ledger.account("n1").slashed_total_micro == 0

    def test_malformed_report_rejected(self):
        with pytest.raises(ValueError):
            MeteredWorkReport(node_id="n1", epoch=1, audits_passed=5, audits_total=4)
        with pytest.raises(ValueError):
            MeteredWorkReport(node_id="n1", epoch=1, bytes_stored=-1)


class TestSlashing:
    def test_slash_uses_progressive_tiers(self):
        ledger = make_ledger()
        bond = 1_000 * MICRO_PER_STABLE
        ledger.post_bond("n1", bond)
        incentive = StableNodeIncentive(ledger)
        # First offense: WARNING tier = 1% of bond.
        penalty = incentive.slash("n1", 1)
        assert penalty == bond * 100 // 10_000
        assert ledger.check_solvency()

    def test_repeat_offenses_escalate(self):
        ledger = make_ledger()
        ledger.post_bond("n1", 1_000 * MICRO_PER_STABLE)
        incentive = StableNodeIncentive(ledger)
        first = incentive.slash("n1", 1)
        second = incentive.slash("n1", 1)  # cumulative count 2 -> MINOR (5%)
        remaining_before_second = 1_000 * MICRO_PER_STABLE - first
        assert second == remaining_before_second * 500 // 10_000
        assert second > first

    def test_zero_failures_no_slash(self):
        incentive = StableNodeIncentive(make_ledger())
        assert incentive.slash("n1", 0) == 0


# ---------------------------------------------------------------------------
# Epoch settlement
# ---------------------------------------------------------------------------


class TestEpochSettlement:
    def test_fully_funded_pays_in_full(self):
        ledger = make_ledger()
        ledger.fund_incentive_budget(10_000)
        incentive = StableNodeIncentive(ledger)
        ledger.account("n1").earned_claim_micro = 3_000
        ledger.account("n2").earned_claim_micro = 2_000
        snapshot = incentive.settle_epoch(1)
        assert snapshot.fully_funded
        assert snapshot.payouts == {"n1": 3_000, "n2": 2_000}
        assert snapshot.carried_claims == {}
        assert ledger.operator_pool_micro == 5_000
        assert ledger.check_solvency()

    def test_underfunded_pays_pro_rata_and_carries_remainder(self):
        ledger = make_ledger()
        ledger.fund_incentive_budget(500)
        incentive = StableNodeIncentive(ledger)
        ledger.account("n1").earned_claim_micro = 600
        ledger.account("n2").earned_claim_micro = 400
        snapshot = incentive.settle_epoch(1)
        assert not snapshot.fully_funded
        assert snapshot.payouts["n1"] == 300  # 600 * 500 / 1000
        assert snapshot.payouts["n2"] == 200
        assert snapshot.carried_claims == {"n1": 300, "n2": 200}
        assert ledger.operator_pool_micro == 0
        assert ledger.check_solvency()

    def test_settlement_never_exceeds_pool(self):
        ledger = make_ledger()
        ledger.fund_incentive_budget(7)
        incentive = StableNodeIncentive(ledger)
        for i, claim in enumerate((13, 17, 19)):
            ledger.account(f"n{i}").earned_claim_micro = claim
        snapshot = incentive.settle_epoch(1)
        assert snapshot.total_paid_micro <= 7
        assert ledger.operator_pool_micro >= 0
        assert ledger.check_solvency()

    def test_carried_claims_paid_when_pool_refills(self):
        ledger = make_ledger()
        incentive = StableNodeIncentive(ledger)
        ledger.account("n1").earned_claim_micro = 1_000
        first = incentive.settle_epoch(1)  # empty pool: nothing paid
        assert first.total_paid_micro == 0
        assert ledger.account("n1").earned_claim_micro == 1_000
        ledger.fund_incentive_budget(1_000)
        second = incentive.settle_epoch(2)
        assert second.total_paid_micro == 1_000
        assert ledger.account("n1").earned_claim_micro == 0

    def test_empty_settlement(self):
        incentive = StableNodeIncentive(make_ledger())
        snapshot = incentive.settle_epoch(1)
        assert snapshot.total_claims_micro == 0
        assert snapshot.total_paid_micro == 0


# ---------------------------------------------------------------------------
# CommitmentPricing
# ---------------------------------------------------------------------------


class TestPricing:
    def test_price_includes_base_fee_and_storage(self):
        ledger = make_ledger()
        pricing = StableCommitmentPricing(ledger)
        cfg = ledger.config
        quote = pricing.price(GIB, 3, SECONDS_PER_MONTH)
        assert quote == cfg.base_commit_fee_micro + 3 * cfg.price_per_gib_month_micro

    def test_price_monotonic_in_size_replication_ttl(self):
        pricing = StableCommitmentPricing(make_ledger())
        base = pricing.price(GIB, 2, SECONDS_PER_MONTH)
        assert pricing.price(2 * GIB, 2, SECONDS_PER_MONTH) >= base
        assert pricing.price(GIB, 4, SECONDS_PER_MONTH) >= base
        assert pricing.price(GIB, 2, 2 * SECONDS_PER_MONTH) >= base

    def test_renew_prices_recorded_commitment_without_base_fee(self):
        ledger = make_ledger()
        pricing = StableCommitmentPricing(ledger)
        pricing.record_commitment("e1", GIB, 2)
        quote = pricing.renew("e1", SECONDS_PER_MONTH)
        assert quote == 2 * ledger.config.price_per_gib_month_micro

    def test_renew_unknown_entity_raises(self):
        pricing = StableCommitmentPricing(make_ledger())
        with pytest.raises(KeyError):
            pricing.renew("missing", 100)

    def test_collect_routes_fee_into_ledger(self):
        ledger = make_ledger()
        pricing = StableCommitmentPricing(ledger)
        quote = pricing.price(GIB, 2, SECONDS_PER_MONTH)
        pricing.collect(quote)
        assert ledger.total_held_micro == quote
        assert ledger.check_solvency()

    def test_fee_to_payout_loop(self):
        """The full loop: user fee in -> proof-gated payout out."""
        ledger = make_ledger()
        pricing = StableCommitmentPricing(ledger)
        incentive = StableNodeIncentive(ledger)
        quote = pricing.price(GIB, 2, SECONDS_PER_MONTH)
        pricing.collect(quote)
        report = MeteredWorkReport(
            node_id="op1",
            epoch=1,
            bytes_stored=GIB,
            seconds_stored=SECONDS_PER_MONTH,
            audits_passed=4,
            audits_total=4,
        )
        incentive.accrue_report(report)
        snapshot = incentive.settle_epoch(1)
        assert snapshot.total_paid_micro > 0
        assert snapshot.total_paid_micro <= quote
        assert ledger.check_solvency()


# ---------------------------------------------------------------------------
# AdmissionControl
# ---------------------------------------------------------------------------


class TestAdmission:
    def test_admission_with_sufficient_bond(self):
        ledger = make_ledger()
        admission = StableAdmissionControl(ledger)
        assert admission.apply("n1", storage_proof=object(), bond=ledger.config.min_bond_micro)
        assert admission.is_admitted("n1")
        assert ledger.account("n1").bond_micro == ledger.config.min_bond_micro
        assert ledger.check_solvency()

    def test_insufficient_bond_rejected(self):
        ledger = make_ledger()
        admission = StableAdmissionControl(ledger)
        decision = admission.apply_detailed(
            "n1", storage_proof=object(), bond=ledger.config.min_bond_micro - 1
        )
        assert decision == AdmissionDecision.REJECTED_BOND
        assert not admission.is_admitted("n1")

    def test_failed_storage_proof_rejected(self):
        ledger = make_ledger()
        admission = StableAdmissionControl(ledger, storage_proof_verifier=lambda p: False)
        decision = admission.apply_detailed(
            "n1", storage_proof=object(), bond=ledger.config.min_bond_micro
        )
        assert decision == AdmissionDecision.REJECTED_PROOF

    def test_zero_bond_config_admits_without_capital(self):
        # The explicit testnet posture: min_bond_micro = 0 must be a
        # deliberate configuration, but when set it works.
        ledger = make_ledger(min_bond_micro=0)
        admission = StableAdmissionControl(ledger)
        assert admission.apply("n1", storage_proof=object(), bond=0)

    def test_eviction_for_fault_forfeits_bond(self):
        ledger = make_ledger()
        admission = StableAdmissionControl(ledger)
        admission.apply("n1", storage_proof=object(), bond=ledger.config.min_bond_micro)
        confirmation = admission.evict("n1", "audit_failure", audit_evidence={"pdp": "fail"})
        assert confirmation.bond_forfeited_micro == ledger.config.min_bond_micro
        assert confirmation.bond_refunded_micro == 0
        assert ledger.insurance_pool_micro == ledger.config.min_bond_micro
        assert ledger.check_solvency()

    def test_voluntary_eviction_refunds_bond(self):
        ledger = make_ledger()
        admission = StableAdmissionControl(ledger)
        admission.apply("n1", storage_proof=object(), bond=ledger.config.min_bond_micro)
        confirmation = admission.evict("n1", "voluntary_exit", audit_evidence=None)
        assert confirmation.bond_refunded_micro == ledger.config.min_bond_micro
        assert confirmation.bond_forfeited_micro == 0
        assert ledger.check_solvency()

    def test_evicted_node_cannot_reapply(self):
        ledger = make_ledger()
        admission = StableAdmissionControl(ledger)
        admission.apply("n1", storage_proof=object(), bond=ledger.config.min_bond_micro)
        admission.evict("n1", "fraud", audit_evidence={"proof": True})
        decision = admission.apply_detailed(
            "n1", storage_proof=object(), bond=ledger.config.min_bond_micro
        )
        assert decision == AdmissionDecision.REJECTED_EVICTED


# ---------------------------------------------------------------------------
# Concurrency — the solvency invariant under parallel movement
# ---------------------------------------------------------------------------


class TestLedgerConcurrency:
    """Regression: balance mutation is read-modify-write, so unsynchronized
    concurrent movement silently lost customer funds AND fabricated money
    the ledger did not hold (observed drift in both directions before the
    lock landed). Real deployments hit this the moment the bridge deposit
    poller credits from its own thread while the gateway debits on the
    request path.
    """

    def _hammer(self, targets):
        import sys
        import threading

        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)  # force preemption inside RMW sequences
        try:
            threads = [threading.Thread(target=fn) for fn in targets]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            sys.setswitchinterval(previous)

    def test_concurrent_deposit_and_debit_preserve_solvency(self):
        ledger = make_ledger()
        ledger.customer_deposit("alice", 10_000_000)
        rounds = 2_000
        shortfalls = []

        def depositor():
            for _ in range(rounds):
                ledger.customer_deposit("alice", 10)

        def debiter():
            for _ in range(rounds):
                try:
                    ledger.customer_debit_to_fees("alice", 10)
                except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                    shortfalls.append(exc)

        self._hammer([depositor, debiter])
        assert shortfalls == []
        assert ledger.check_solvency()
        # Exact accounting, not just "solvent": every micro landed once.
        assert ledger.customer_balance("alice") == 10_000_000
        assert (
            ledger.operator_pool_micro + ledger.insurance_pool_micro + (ledger.treasury_micro)
            == rounds * 10
        )

    def test_concurrent_debits_cannot_double_spend_one_balance(self):
        ledger = make_ledger()
        ledger.customer_deposit("alice", 1_000)  # funds exactly 100 debits of 10
        succeeded = []

        def debiter():
            for _ in range(200):
                try:
                    ledger.customer_debit_to_fees("alice", 10)
                    succeeded.append(1)
                except Exception:  # noqa: BLE001 - expected once drained
                    pass

        self._hammer([debiter, debiter])
        assert len(succeeded) == 100, "balance was spent more than once"
        assert ledger.customer_balance("alice") == 0
        assert ledger.check_solvency()

    def test_concurrent_pool_payouts_never_overdraw(self):
        ledger = make_ledger()
        ledger.fund_incentive_budget(1_000)
        paid = []

        def payer(node):
            def run():
                for _ in range(200):
                    paid.append(ledger.pay_from_pool(node, 10))

            return run

        self._hammer([payer("n1"), payer("n2")])
        assert sum(paid) == 1_000, "pool paid out more or less than it held"
        assert ledger.operator_pool_micro == 0
        assert ledger.check_solvency()
