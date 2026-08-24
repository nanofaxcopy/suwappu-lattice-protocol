"""
Stablecoin-denominated compute incentives for the LTP operator network.

This module implements the whitepaper §5.5 economic interfaces
(``NodeIncentive``, ``CommitmentPricing``, ``AdmissionControl``) for the
deployment model adopted in
``docs/economics/DEFERRED_TOKEN_ARCHITECTURE.md``: there is no native
token. Operators bond, earn, and are slashed **in the stablecoin the
network settles** (USDC-style 6-decimal base units, called "micro" here).

Design rules (the load-bearing ones):

  1. **No minting.** Every unit paid out was first deposited — either as
     a commitment/bridge fee (``StablecoinLedger.deposit_fee``) or as an
     explicitly funded incentive budget
     (``StablecoinLedger.fund_incentive_budget``). The ledger enforces a
     hard solvency invariant: payouts can never exceed what the pool
     actually holds. If earned claims outrun the pool, settlement pays
     pro-rata and carries the remainder as an outstanding claim — it
     never fabricates balance.

  2. **Proof-gated pay.** Compensation is metered against *audited* work.
     A ``MeteredWorkReport`` with zero passed audits earns zero: no
     proof, no pay. Audit failures route through ``slash`` against the
     operator's stablecoin bond.

  3. **Interface conformance.** ``StableNodeIncentive``,
     ``StableCommitmentPricing``, and ``StableAdmissionControl`` satisfy
     the §5.5 interface signatures verbatim, so this module is one
     deployment-specific backing of the whitepaper's token-agnostic
     interface layer (public deployment column of the §5.5 table),
     not a change to it.

  4. **No burn.** Unlike ``ltp.economics`` (which models a native token
     and burns a fee share), a stablecoin cannot be meaningfully burned
     by this network — the "burn" share is redirected to the protocol
     treasury. Slashed bonds go to the insurance pool, mirroring the
     suwappu-dag §8.3 waterfall (counterparty → insurance → treasury,
     never burn).

Slashing severity reuses the progressive tiers from ``ltp.economics``
(``SlashingTier`` / ``SLASHING_RATES``) so the two models stay
comparable; what changes is the asset the penalty is taken in.

Cross-repo context: the corresponding settlement surface on the SUWAPPU
DAG chain is ``suwappu-dag/crates/suwappu-precompiles/src/rewards.rs``,
where per-epoch validator payouts are minted through the registered-
issuer precompile only when the reserve-coverage circuit breaker
attests the stablecoin is fully backed. Both sides enforce the same
principle from different directions: **a compute payout exists only if
the stablecoin backing it does.** See
``docs/economics/VALIDATOR_COMPUTE_INCENTIVES.md`` for the full design.

This module is intentionally NOT re-exported from ``ltp.__init__`` yet:
per ``docs/STABILITY_PROMISES.md`` that keeps the surface private and
iterable until the parameterization has survived a testnet cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable

from .economics import SLASHING_RATES, tier_for_offense_count

__all__ = [
    "AdmissionControl",
    "AdmissionDecision",
    "CommitmentPricing",
    "EvictionConfirmation",
    "EpochPayoutSnapshot",
    "IncentiveConfig",
    "MeteredWorkReport",
    "NodeIncentive",
    "NodeIncentiveAccount",
    "StableAdmissionControl",
    "StableCommitmentPricing",
    "StableNodeIncentive",
    "StablecoinLedger",
    "GIB",
    "MICRO_PER_STABLE",
    "SECONDS_PER_MONTH",
]


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

MICRO_PER_STABLE = 10**6  # USDC-style 6-decimal base units per whole unit
GIB = 2**30  # bytes per GiB
SECONDS_PER_MONTH = 30 * 24 * 3600  # 30-day month, matches pricing quotes


# ---------------------------------------------------------------------------
# Whitepaper §5.5 interfaces (verbatim signatures)
# ---------------------------------------------------------------------------


@runtime_checkable
class NodeIncentive(Protocol):
    """Whitepaper §5.5 ``NodeIncentive`` interface."""

    def compensate(
        self, node_id: str, bytes_stored: int, seconds_stored: int, bytes_served: int
    ) -> int:
        """Meter a reward for storage and serving work. Returns micro."""
        ...

    def slash(self, node_id: str, audit_failure_count: int) -> int:
        """Penalize audit failures. Returns the penalty amount in micro."""
        ...


@runtime_checkable
class CommitmentPricing(Protocol):
    """Whitepaper §5.5 ``CommitmentPricing`` interface."""

    def price(self, entity_size: int, replication_factor: int, ttl_seconds: int) -> int:
        """Quote the cost of a new commitment. Returns micro."""
        ...

    def renew(self, entity_id: str, additional_ttl: int) -> int:
        """Quote the cost of extending an existing commitment. Returns micro."""
        ...


@runtime_checkable
class AdmissionControl(Protocol):
    """Whitepaper §5.5 ``AdmissionControl`` interface."""

    def apply(self, node_identity: str, storage_proof: Any, bond: int) -> bool:
        """Admit or reject an applicant node."""
        ...

    def evict(self, node_id: str, reason: str, audit_evidence: Any) -> "EvictionConfirmation":
        """Evict a node, settling its bond."""
        ...


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class IncentiveConfig:
    """Tunable parameters, all denominated in stablecoin micro-units.

    Defaults are a mainnet *posture*, not a tuned schedule: non-zero
    bond, non-zero prices. A testnet that intentionally zeroes
    ``min_bond_micro`` should say so in deployment docs (see the B6
    finding in ``docs/plans/2026-08-04-external-validator-onboarding.md``
    about zero bonds being indistinguishable from misconfiguration).
    """

    # --- Metering rates ---
    price_per_gib_month_micro: int = 20_000  # $0.02 / GiB-month stored
    price_per_gib_served_micro: int = 10_000  # $0.01 / GiB served
    base_commit_fee_micro: int = 1_000  # $0.001 flat per commitment

    # --- Fee split (basis points, must sum to 10000) ---
    fee_operator_share_bps: int = 8_000  # 80% funds operator payouts
    fee_insurance_share_bps: int = 1_000  # 10% insurance pool
    fee_treasury_share_bps: int = 1_000  # 10% treasury (replaces "burn")

    # --- Bonds ---
    min_bond_micro: int = 1_000 * MICRO_PER_STABLE  # $1,000 to operate

    # --- Audits ---
    # Audit failures within one report beyond this count trigger a slash
    # (a single isolated failure only discounts pay for that epoch).
    slash_failure_threshold: int = 2

    def __post_init__(self) -> None:
        total_bps = (
            self.fee_operator_share_bps + self.fee_insurance_share_bps + self.fee_treasury_share_bps
        )
        if total_bps != 10_000:
            raise ValueError(f"Fee split must sum to 10000 bps, got {total_bps}")
        if self.min_bond_micro < 0:
            raise ValueError("min_bond_micro must be non-negative")

    def storage_rate_micro(self, byte_seconds: int) -> int:
        """Micro owed for a number of byte-seconds stored."""
        return byte_seconds * self.price_per_gib_month_micro // (GIB * SECONDS_PER_MONTH)

    def serving_rate_micro(self, bytes_served: int) -> int:
        """Micro owed for a number of bytes served."""
        return bytes_served * self.price_per_gib_served_micro // GIB


# ---------------------------------------------------------------------------
# Ledger — the solvency core
# ---------------------------------------------------------------------------


class LedgerError(Exception):
    """Raised on operations that would violate ledger solvency."""


@dataclass
class NodeIncentiveAccount:
    """Per-node stablecoin account state."""

    node_id: str
    bond_micro: int = 0
    earned_claim_micro: int = 0  # accrued, not yet paid
    paid_total_micro: int = 0
    slashed_total_micro: int = 0
    audit_offense_count: int = 0
    evicted: bool = False


class StablecoinLedger:
    """Book-keeping for every stablecoin unit the incentive layer holds.

    Solvency invariant (checked, not assumed): the sum of the operator
    pool, insurance pool, treasury, and all node bonds always equals
    total deposits minus total withdrawals. Nothing here can mint.
    """

    def __init__(self, config: IncentiveConfig | None = None) -> None:
        self.config = config or IncentiveConfig()
        self.operator_pool_micro: int = 0
        self.insurance_pool_micro: int = 0
        self.treasury_micro: int = 0
        self._accounts: dict[str, NodeIncentiveAccount] = {}
        self._total_deposited: int = 0
        self._total_withdrawn: int = 0

    # --- Accounts ---

    def account(self, node_id: str) -> NodeIncentiveAccount:
        """Fetch (or lazily create) the account for ``node_id``."""
        if node_id not in self._accounts:
            self._accounts[node_id] = NodeIncentiveAccount(node_id=node_id)
        return self._accounts[node_id]

    def accounts(self) -> list[NodeIncentiveAccount]:
        """All accounts, in insertion order."""
        return list(self._accounts.values())

    # --- Inflows ---

    def deposit_fee(self, amount_micro: int) -> tuple[int, int, int]:
        """Deposit a commitment/bridge fee and split it.

        Returns ``(operator_share, insurance_share, treasury_share)``.
        The remainder from integer division lands in the operator pool
        so no unit is lost.
        """
        if amount_micro < 0:
            raise LedgerError("fee deposit must be non-negative")
        cfg = self.config
        insurance = amount_micro * cfg.fee_insurance_share_bps // 10_000
        treasury = amount_micro * cfg.fee_treasury_share_bps // 10_000
        operator = amount_micro - insurance - treasury
        self.operator_pool_micro += operator
        self.insurance_pool_micro += insurance
        self.treasury_micro += treasury
        self._total_deposited += amount_micro
        return operator, insurance, treasury

    def fund_incentive_budget(self, amount_micro: int) -> None:
        """Deposit an externally funded incentive budget (no split).

        This is the deferred-token replacement for a bootstrap subsidy:
        a *funded* pool of real stablecoins, not an emission schedule.
        """
        if amount_micro < 0:
            raise LedgerError("budget funding must be non-negative")
        self.operator_pool_micro += amount_micro
        self._total_deposited += amount_micro

    def post_bond(self, node_id: str, amount_micro: int) -> None:
        """Deposit ``amount_micro`` into ``node_id``'s bond."""
        if amount_micro < 0:
            raise LedgerError("bond must be non-negative")
        self.account(node_id).bond_micro += amount_micro
        self._total_deposited += amount_micro

    # --- Internal movements ---

    def pay_from_pool(self, node_id: str, amount_micro: int) -> int:
        """Pay ``node_id`` up to ``amount_micro`` from the operator pool.

        Pays ``min(amount, pool balance)`` — the solvency clamp — and
        returns the amount actually paid.
        """
        if amount_micro < 0:
            raise LedgerError("payment must be non-negative")
        paid = min(amount_micro, self.operator_pool_micro)
        self.operator_pool_micro -= paid
        acct = self.account(node_id)
        acct.paid_total_micro += paid
        self._total_withdrawn += paid
        return paid

    def slash_bond(self, node_id: str, amount_micro: int) -> int:
        """Move up to ``amount_micro`` from the node's bond to insurance.

        Returns the amount actually slashed (clamped to the bond).
        """
        if amount_micro < 0:
            raise LedgerError("slash must be non-negative")
        acct = self.account(node_id)
        slashed = min(amount_micro, acct.bond_micro)
        acct.bond_micro -= slashed
        acct.slashed_total_micro += slashed
        self.insurance_pool_micro += slashed
        return slashed

    def refund_bond(self, node_id: str) -> int:
        """Return a node's remaining bond (withdrawal). Returns amount."""
        acct = self.account(node_id)
        refund = acct.bond_micro
        acct.bond_micro = 0
        self._total_withdrawn += refund
        return refund

    def forfeit_bond_to_insurance(self, node_id: str) -> int:
        """Forfeit a node's entire remaining bond to the insurance pool."""
        acct = self.account(node_id)
        forfeited = acct.bond_micro
        acct.bond_micro = 0
        acct.slashed_total_micro += forfeited
        self.insurance_pool_micro += forfeited
        return forfeited

    # --- Invariant ---

    @property
    def total_held_micro(self) -> int:
        """Every micro the ledger currently holds, across all buckets."""
        bonds = sum(a.bond_micro for a in self._accounts.values())
        return self.operator_pool_micro + self.insurance_pool_micro + self.treasury_micro + bonds

    def check_solvency(self) -> bool:
        """True iff held == deposited - withdrawn. Never mints, never leaks."""
        return self.total_held_micro == self._total_deposited - self._total_withdrawn


# ---------------------------------------------------------------------------
# Metered, proof-gated work reports
# ---------------------------------------------------------------------------


@dataclass
class MeteredWorkReport:
    """One node's audited work for one epoch.

    ``audits_passed`` / ``audits_total`` come from the PDP audit cycle
    (``ltp.enforcement.PDPVerifier`` driven by the node's
    ``AuditScheduler``). Compensation scales with the pass ratio; a
    report with zero audits earns zero — unproven work is unpaid work.
    """

    node_id: str
    epoch: int
    bytes_stored: int = 0
    seconds_stored: int = 0
    bytes_served: int = 0
    audits_passed: int = 0
    audits_total: int = 0

    def __post_init__(self) -> None:
        if (
            min(
                self.bytes_stored,
                self.seconds_stored,
                self.bytes_served,
                self.audits_passed,
                self.audits_total,
            )
            < 0
        ):
            raise ValueError("work report fields must be non-negative")
        if self.audits_passed > self.audits_total:
            raise ValueError("audits_passed cannot exceed audits_total")

    @property
    def audit_failures(self) -> int:
        """Failed audits in this report."""
        return self.audits_total - self.audits_passed


@dataclass
class EpochPayoutSnapshot:
    """Result of one epoch settlement, for dashboards and audit trails."""

    epoch: int
    total_claims_micro: int
    total_paid_micro: int
    pool_before_micro: int
    pool_after_micro: int
    payouts: dict[str, int] = field(default_factory=dict)
    carried_claims: dict[str, int] = field(default_factory=dict)

    @property
    def fully_funded(self) -> bool:
        """True iff every claim was paid in full this epoch."""
        return self.total_paid_micro == self.total_claims_micro


# ---------------------------------------------------------------------------
# NodeIncentive implementation
# ---------------------------------------------------------------------------


class StableNodeIncentive:
    """§5.5 ``NodeIncentive`` backed by the stablecoin ledger.

    ``compensate`` meters and accrues a claim; ``settle_epoch`` pays
    claims from the operator pool, pro-rata when underfunded. The split
    keeps the §5.5 signature intact while making it impossible for the
    metering path to overdraw the pool.
    """

    def __init__(self, ledger: StablecoinLedger) -> None:
        self.ledger = ledger
        self.config = ledger.config

    # --- §5.5 surface ---

    def compensate(
        self, node_id: str, bytes_stored: int, seconds_stored: int, bytes_served: int
    ) -> int:
        """Meter a reward and accrue it as a claim. Returns micro accrued."""
        if min(bytes_stored, seconds_stored, bytes_served) < 0:
            raise ValueError("metering inputs must be non-negative")
        cfg = self.config
        reward = cfg.storage_rate_micro(bytes_stored * seconds_stored)
        reward += cfg.serving_rate_micro(bytes_served)
        acct = self.ledger.account(node_id)
        if acct.evicted:
            return 0
        acct.earned_claim_micro += reward
        return reward

    def slash(self, node_id: str, audit_failure_count: int) -> int:
        """Slash the node's bond for audit failures. Returns micro slashed.

        Failures accumulate on the account's offense counter; severity
        follows the progressive tiers shared with ``ltp.economics``.
        """
        if audit_failure_count <= 0:
            return 0
        acct = self.ledger.account(node_id)
        acct.audit_offense_count += audit_failure_count
        tier = tier_for_offense_count(acct.audit_offense_count)
        rate_bps = SLASHING_RATES[tier]
        penalty = acct.bond_micro * rate_bps // 10_000
        return self.ledger.slash_bond(node_id, penalty)

    # --- Proof-gated accrual + settlement ---

    def accrue_report(self, report: MeteredWorkReport) -> int:
        """Accrue a claim for an audited work report. Returns micro accrued.

        Pay is scaled by the audit pass ratio (zero audits ⇒ zero pay),
        and failures beyond ``slash_failure_threshold`` trigger a slash.
        """
        acct = self.ledger.account(report.node_id)
        if acct.evicted or report.audits_total == 0:
            accrued = 0
        else:
            cfg = self.config
            metered = cfg.storage_rate_micro(report.bytes_stored * report.seconds_stored)
            metered += cfg.serving_rate_micro(report.bytes_served)
            accrued = metered * report.audits_passed // report.audits_total
            acct.earned_claim_micro += accrued
        if report.audit_failures >= self.config.slash_failure_threshold:
            self.slash(report.node_id, report.audit_failures)
        return accrued

    def settle_epoch(self, epoch: int) -> EpochPayoutSnapshot:
        """Pay accrued claims from the operator pool, pro-rata if short.

        Unpaid remainders stay on the account as carried claims — they
        are owed, and payable once the pool is funded, but are never
        paid from balance the pool does not hold.
        """
        ledger = self.ledger
        accounts = [a for a in ledger.accounts() if a.earned_claim_micro > 0]
        total_claims = sum(a.earned_claim_micro for a in accounts)
        pool_before = ledger.operator_pool_micro
        snapshot = EpochPayoutSnapshot(
            epoch=epoch,
            total_claims_micro=total_claims,
            total_paid_micro=0,
            pool_before_micro=pool_before,
            pool_after_micro=pool_before,
        )
        if total_claims == 0:
            return snapshot

        budget = min(total_claims, pool_before)
        for acct in accounts:
            # Pro-rata by claim; floor division keeps the sum <= budget.
            share = acct.earned_claim_micro * budget // total_claims
            paid = ledger.pay_from_pool(acct.node_id, share)
            acct.earned_claim_micro -= paid
            snapshot.payouts[acct.node_id] = paid
            snapshot.total_paid_micro += paid
            if acct.earned_claim_micro > 0:
                snapshot.carried_claims[acct.node_id] = acct.earned_claim_micro

        snapshot.pool_after_micro = ledger.operator_pool_micro
        return snapshot


# ---------------------------------------------------------------------------
# CommitmentPricing implementation
# ---------------------------------------------------------------------------


class StableCommitmentPricing:
    """§5.5 ``CommitmentPricing`` in stablecoin micro-units.

    Pricing follows the whitepaper §6.4 cost model: the payer funds
    ``entity_size × replication_factor`` bytes of storage for the TTL,
    plus a flat base fee. ``collect`` routes revenue into the ledger,
    which is what ultimately funds ``NodeIncentive`` payouts — closing
    the loop fee-in → proof-gated payout-out.
    """

    def __init__(self, ledger: StablecoinLedger) -> None:
        self.ledger = ledger
        self.config = ledger.config
        self._commitments: dict[str, tuple[int, int]] = {}  # entity_id -> (size, repl)

    def price(self, entity_size: int, replication_factor: int, ttl_seconds: int) -> int:
        """Quote a new commitment. Returns micro."""
        if entity_size < 0 or replication_factor < 1 or ttl_seconds < 0:
            raise ValueError("invalid pricing inputs")
        stored_byte_seconds = entity_size * replication_factor * ttl_seconds
        return self.config.base_commit_fee_micro + self.config.storage_rate_micro(
            stored_byte_seconds
        )

    def record_commitment(self, entity_id: str, entity_size: int, replication_factor: int) -> None:
        """Register a commitment so ``renew`` can price it later."""
        if entity_size < 0 or replication_factor < 1:
            raise ValueError("invalid commitment parameters")
        self._commitments[entity_id] = (entity_size, replication_factor)

    def renew(self, entity_id: str, additional_ttl: int) -> int:
        """Quote a TTL extension for a recorded commitment. Returns micro."""
        if entity_id not in self._commitments:
            raise KeyError(f"unknown entity_id: {entity_id}")
        if additional_ttl < 0:
            raise ValueError("additional_ttl must be non-negative")
        size, repl = self._commitments[entity_id]
        # No base fee on renewal — the commitment already exists.
        return self.config.storage_rate_micro(size * repl * additional_ttl)

    def collect(self, amount_micro: int) -> tuple[int, int, int]:
        """Collect a quoted fee into the ledger. Returns the split."""
        return self.ledger.deposit_fee(amount_micro)


# ---------------------------------------------------------------------------
# AdmissionControl implementation
# ---------------------------------------------------------------------------


class AdmissionDecision(Enum):
    """Outcome of an admission application."""

    ACCEPTED = "accepted"
    REJECTED_BOND = "rejected_bond"
    REJECTED_PROOF = "rejected_proof"
    REJECTED_EVICTED = "rejected_evicted"


@dataclass
class EvictionConfirmation:
    """Outcome of an eviction: what happened to the node's bond."""

    node_id: str
    reason: str
    bond_refunded_micro: int
    bond_forfeited_micro: int


# Eviction reasons whose remaining bond is forfeited to insurance rather
# than refunded: the operator's misbehavior is what the bond was for.
FORFEITING_REASONS = frozenset({"audit_failure", "fraud", "equivocation", "withholding"})


class StableAdmissionControl:
    """§5.5 ``AdmissionControl`` with a stablecoin bond.

    ``storage_proof`` is verified by an injected verifier callable
    (default: truthiness), so deployments can plug in a real PDP check
    (``ltp.enforcement.PDPVerifier``) without this module importing the
    whole enforcement pipeline.
    """

    def __init__(
        self,
        ledger: StablecoinLedger,
        storage_proof_verifier: Callable[[Any], bool] | None = None,
    ) -> None:
        self.ledger = ledger
        self.config = ledger.config
        self._verify_proof = storage_proof_verifier or bool
        self._admitted: set[str] = set()

    def is_admitted(self, node_id: str) -> bool:
        """Whether ``node_id`` is currently admitted."""
        return node_id in self._admitted

    def apply(self, node_identity: str, storage_proof: Any, bond: int) -> bool:
        """Admit ``node_identity`` if the bond and storage proof check out."""
        return self.apply_detailed(node_identity, storage_proof, bond) == (
            AdmissionDecision.ACCEPTED
        )

    def apply_detailed(
        self, node_identity: str, storage_proof: Any, bond: int
    ) -> AdmissionDecision:
        """Like ``apply`` but returns the specific decision."""
        acct = self.ledger.account(node_identity)
        if acct.evicted:
            return AdmissionDecision.REJECTED_EVICTED
        if bond + acct.bond_micro < self.config.min_bond_micro:
            return AdmissionDecision.REJECTED_BOND
        if not self._verify_proof(storage_proof):
            return AdmissionDecision.REJECTED_PROOF
        if bond > 0:
            self.ledger.post_bond(node_identity, bond)
        self._admitted.add(node_identity)
        return AdmissionDecision.ACCEPTED

    def evict(self, node_id: str, reason: str, audit_evidence: Any) -> EvictionConfirmation:
        """Evict ``node_id``; forfeit or refund the remaining bond by reason."""
        acct = self.ledger.account(node_id)
        acct.evicted = True
        self._admitted.discard(node_id)
        if reason in FORFEITING_REASONS and audit_evidence is not None:
            forfeited = self.ledger.forfeit_bond_to_insurance(node_id)
            return EvictionConfirmation(
                node_id=node_id,
                reason=reason,
                bond_refunded_micro=0,
                bond_forfeited_micro=forfeited,
            )
        refunded = self.ledger.refund_bond(node_id)
        return EvictionConfirmation(
            node_id=node_id,
            reason=reason,
            bond_refunded_micro=refunded,
            bond_forfeited_micro=0,
        )
