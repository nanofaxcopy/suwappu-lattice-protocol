"""GatewayTracker — attestation lifecycle tracking for the gateway VM.

Tracks attestations through: PENDING → SUBMITTED → CONFIRMED → FINALIZED,
with FAILED as a terminal error state. Thread-safe, returns snapshot dicts.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from .writer import GatewayAttestation


class GatewayTracker:
    """Tracks gateway attestation lifecycle. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        self._tx_hash_index: dict[str, str] = {}  # tx_hash → event_id

    def mark_pending(self, attestation: GatewayAttestation) -> None:
        """Record a new attestation as pending submission."""
        with self._lock:
            self._records[attestation.event_id] = {
                "event_id": attestation.event_id,
                "source_chain_id": attestation.source_chain_id,
                "dest_chain_id": attestation.dest_chain_id,
                "digest": attestation.digest,
                "status": "pending",
                "tx_hash": "",
                "block_number": 0,
                "gas_used": 0,
                "error": "",
                "created_at": time.time(),
                "submitted_at": 0.0,
                "confirmed_at": 0.0,
            }

    def mark_submitted(self, event_id: str, *, tx_hash: str) -> None:
        """Transition PENDING → SUBMITTED."""
        with self._lock:
            rec = self._records.get(event_id)
            if rec is None:
                raise KeyError(f"unknown event_id: {event_id}")
            rec["status"] = "submitted"
            rec["tx_hash"] = tx_hash
            rec["submitted_at"] = time.time()
            self._tx_hash_index[tx_hash] = event_id

    def mark_confirmed(self, event_id: str, *, block_number: int, gas_used: int) -> None:
        """Transition SUBMITTED → CONFIRMED."""
        with self._lock:
            rec = self._records.get(event_id)
            if rec is None:
                raise KeyError(f"unknown event_id: {event_id}")
            rec["status"] = "confirmed"
            rec["block_number"] = block_number
            rec["gas_used"] = gas_used
            rec["confirmed_at"] = time.time()

    def mark_finalized(self, event_id: str) -> None:
        """Transition CONFIRMED → FINALIZED (terminal success)."""
        with self._lock:
            rec = self._records.get(event_id)
            if rec is None:
                raise KeyError(f"unknown event_id: {event_id}")
            rec["status"] = "finalized"

    def mark_failed(self, event_id: str, *, error: str) -> None:
        """Transition any non-terminal → FAILED (terminal error)."""
        with self._lock:
            rec = self._records.get(event_id)
            if rec is None:
                raise KeyError(f"unknown event_id: {event_id}")
            rec["status"] = "failed"
            rec["error"] = error

    def get(self, event_id: str) -> Optional[dict]:
        """Return snapshot of an attestation record, or None."""
        with self._lock:
            rec = self._records.get(event_id)
            return dict(rec) if rec is not None else None

    def get_by_status(self, status: str) -> list[dict]:
        """Return all records with a given status."""
        with self._lock:
            return [dict(r) for r in self._records.values() if r["status"] == status]

    def lookup_by_tx_hash(self, tx_hash: str) -> Optional[dict]:
        """Find an attestation record by its submission tx hash."""
        with self._lock:
            event_id = self._tx_hash_index.get(tx_hash)
            if event_id is None:
                return None
            rec = self._records.get(event_id)
            return dict(rec) if rec is not None else None

    def stats(self) -> dict[str, int]:
        """Count records by status."""
        counts = {"pending": 0, "submitted": 0, "confirmed": 0, "finalized": 0, "failed": 0}
        with self._lock:
            for rec in self._records.values():
                status = rec["status"]
                if status in counts:
                    counts[status] += 1
        return counts
