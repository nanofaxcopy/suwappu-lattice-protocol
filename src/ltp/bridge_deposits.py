"""
Bridge deposit watcher — the last link between a customer's stablecoins
and a served token.

Customers fund their prepaid inference balance by sending stablecoins
across the bridge. This module turns observed bridge transfers into
``StablecoinLedger.customer_deposit`` credits, safely:

  1. **Idempotent by transaction hash.** A transfer credits exactly
     once, however many times the event source replays it — the same
     one-settlement posture as inference receipts and the issuer
     precompile's two-phase burn.

  2. **Confirmation-gated.** Events below ``min_confirmations`` are not
     credited and not remembered — they simply credit on a later poll
     once the chain has buried them. Reorg exposure is bounded by the
     confirmation depth, not by trusting the first sighting.

  3. **Attribution is explicit.** A sender address credits a customer
     only if the address was bound to that customer first
     (``bind_address``). Unbound transfers are quarantined in an
     unattributed list for operations to resolve — money is never
     guessed into an account, and never dropped silently.

The event source is injected: ``poll_once`` takes any callable that
returns ``DepositEvent``s, so the production chain client (watching the
``BridgeEmitter.BridgeTransfer`` event: sender, recipient, payloadHash,
amount, nonce) and the test source plug in identically. The chain-side
adapter is deployment wiring; the crediting rules live here.

Like the rest of the inference stack, this module is NOT re-exported
from ``ltp.__init__`` (private per ``docs/STABILITY_PROMISES.md``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable

from .incentives import StablecoinLedger

__all__ = [
    "CreditedDeposit",
    "DepositEvent",
    "DepositWatcher",
    "DepositError",
]

_TX_HASH = re.compile(r"^0x[0-9a-f]{64}$")
_ADDRESS = re.compile(r"^0x[0-9a-f]{40}$")


class DepositError(Exception):
    """Raised on invalid bindings or malformed deposit events."""


def _normalize_address(address: str) -> str:
    """Lowercase and validate a 0x-prefixed 20-byte address."""
    normalized = address.strip().lower()
    if not _ADDRESS.match(normalized):
        raise DepositError(f"not a valid address: {address!r}")
    return normalized


@dataclass
class DepositEvent:
    """One observed bridge transfer toward the inference deposit pool.

    ``confirmations`` is how deep the containing block is at observation
    time — the watcher, not the event source, decides when that is deep
    enough to credit.
    """

    tx_hash: str
    sender: str
    amount_micro: int
    confirmations: int

    def __post_init__(self) -> None:
        self.tx_hash = self.tx_hash.strip().lower()
        if not _TX_HASH.match(self.tx_hash):
            raise DepositError(f"not a valid tx hash: {self.tx_hash!r}")
        self.sender = _normalize_address(self.sender)
        if self.amount_micro < 0:
            raise DepositError("amount_micro must be non-negative")
        if self.confirmations < 0:
            raise DepositError("confirmations must be non-negative")


@dataclass
class CreditedDeposit:
    """Record of one credit applied to a customer balance."""

    tx_hash: str
    customer_id: str
    amount_micro: int
    balance_after_micro: int


class DepositWatcher:
    """Credits confirmed, attributed bridge deposits into the ledger."""

    def __init__(self, ledger: StablecoinLedger, min_confirmations: int = 6) -> None:
        if min_confirmations < 0:
            raise DepositError("min_confirmations must be non-negative")
        self.ledger = ledger
        self.min_confirmations = min_confirmations
        self._bindings: dict[str, str] = {}
        self._credited_tx: set[str] = set()
        self._unattributed: dict[str, DepositEvent] = {}

    # --- Attribution ---

    def bind_address(self, address: str, customer_id: str) -> None:
        """Bind a sender address to the customer its deposits credit.

        Rebinding to a *different* customer is refused — an address's
        deposits must never silently start crediting someone else. Undo
        a binding explicitly with ``unbind_address`` first.
        """
        if not customer_id:
            raise DepositError("customer_id must be non-empty")
        normalized = _normalize_address(address)
        existing = self._bindings.get(normalized)
        if existing is not None and existing != customer_id:
            raise DepositError(f"address {normalized} already bound to customer {existing}")
        self._bindings[normalized] = customer_id

    def unbind_address(self, address: str) -> None:
        """Remove an address binding (future deposits become unattributed)."""
        self._bindings.pop(_normalize_address(address), None)

    def customer_for(self, address: str) -> str | None:
        """The customer an address is bound to, if any."""
        return self._bindings.get(_normalize_address(address))

    # --- Crediting ---

    def process(self, events: Iterable[DepositEvent]) -> list[CreditedDeposit]:
        """Apply crediting rules to a batch of observed events.

        Returns the credits applied this call. Under-confirmed events
        are skipped without state (they credit on a later poll);
        unattributed events are quarantined for operations; duplicates
        are ignored.
        """
        credited: list[CreditedDeposit] = []
        for event in events:
            if event.tx_hash in self._credited_tx:
                continue
            if event.confirmations < self.min_confirmations:
                continue
            customer_id = self._bindings.get(event.sender)
            if customer_id is None:
                self._unattributed[event.tx_hash] = event
                continue
            balance = self.ledger.customer_deposit(customer_id, event.amount_micro)
            self._credited_tx.add(event.tx_hash)
            self._unattributed.pop(event.tx_hash, None)
            credited.append(
                CreditedDeposit(
                    tx_hash=event.tx_hash,
                    customer_id=customer_id,
                    amount_micro=event.amount_micro,
                    balance_after_micro=balance,
                )
            )
        return credited

    def poll_once(self, source: Callable[[], Iterable[DepositEvent]]) -> list[CreditedDeposit]:
        """Pull one batch from an event source and process it."""
        return self.process(source())

    # --- Introspection ---

    @property
    def credited_count(self) -> int:
        """Number of distinct transactions credited so far."""
        return len(self._credited_tx)

    def is_credited(self, tx_hash: str) -> bool:
        """Whether a transaction hash has already been credited."""
        return tx_hash.strip().lower() in self._credited_tx

    def unattributed(self) -> list[DepositEvent]:
        """Confirmed deposits from unbound addresses, awaiting resolution.

        Once the address is bound (``bind_address``), re-processing the
        event — every poll returns it again until credited — applies it.
        """
        return list(self._unattributed.values())
