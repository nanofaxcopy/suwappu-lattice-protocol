"""GatewayVMService — POA attestation gateway daemon."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..keypair import KeyPair
from .config import GatewayVMConfig
from .events import BridgeEvent
from .listener import EventListener
from .metrics import create_gateway_metrics
from .replay import ReplayDB
from .validator import EventValidator
from .writer import AttestationWriter, GatewayAttestation

logger = logging.getLogger(__name__)

__all__ = ["GatewayVMService", "GatewayVMTickResult"]


@dataclass
class GatewayVMTickResult:
    """Result of a single gateway tick."""

    epoch: int = 0
    events_observed: int = 0
    events_accepted: int = 0
    events_rejected: int = 0
    anchor_failures: int = 0
    retries_attempted: int = 0
    error: str = ""


class GatewayVMService:
    """POA attestation gateway daemon.

    Follows the BridgeOperatorService pattern: daemon thread, tick(),
    start()/stop(), epoch counter. tick() is public for deterministic
    testing without threads.

    Each tick:
      1. Process retry queue
      2. Poll source chain for new bridge events
      3. Validate each event (12-point checklist)
      4. Create ML-DSA-65 signed attestation
      5. Anchor attestation to devnet
      6. Mark event as processed in replay DB
    """

    def __init__(
        self,
        config: GatewayVMConfig,
        operator_keypair: Optional[KeyPair],
        fetch_logs: Callable[[int, int], list[dict]],
        get_source_block_number: Callable[[], int],
        get_dest_block_number: Callable[[], int],
        anchor_fn: Callable[[GatewayAttestation], str],
        is_signer_authorized: Callable[[], bool],
        metrics_registry=None,
    ) -> None:
        if operator_keypair is None:
            raise TypeError(
                "operator_keypair is required — gateway attestations must be signed"
            )
        self._config = config
        self._keypair = operator_keypair

        # Components
        self._listener = EventListener(
            source_chain_id=config.source_chain_id,
            fetch_logs=fetch_logs,
            get_block_number=get_source_block_number,
        )
        self._replay_db = ReplayDB(config.replay_db_path)
        self._validator = EventValidator(
            config=config,
            replay_db=self._replay_db,
            get_block_number=get_source_block_number,
            is_signer_authorized=is_signer_authorized,
        )
        self._writer = AttestationWriter(
            operator_keypair=operator_keypair,
            dest_chain_id=config.dest_chain_id,
        )
        self._anchor_fn = anchor_fn
        self._get_dest_block = get_dest_block_number

        # Retry queue: [(attestation, event, attempt_count)]
        self._retry_queue: list[tuple[GatewayAttestation, BridgeEvent, int]] = []
        self._data_lock = threading.Lock()

        # Threading (mirrors BridgeOperatorService)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._epoch_lock = threading.Lock()
        self._epoch = 0

        # Metrics (optional)
        self._metrics = None
        if metrics_registry is not None:
            self._metrics = create_gateway_metrics(metrics_registry)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch daemon thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"gateway-vm-{self._config.gateway_id}",
        )
        self._thread.start()
        logger.info(
            "GatewayVMService[%s] started (source=%d, dest=%d, interval=%.1fs)",
            self._config.gateway_id,
            self._config.source_chain_id,
            self._config.dest_chain_id,
            self._config.poll_interval_seconds,
        )

    def stop(self) -> None:
        """Signal shutdown and join thread."""
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._config.poll_interval_seconds + 5)
            self._thread = None
        if self._replay_db is not None:
            self._replay_db.close()
        logger.info(
            "GatewayVMService[%s] stopped (epoch=%d)",
            self._config.gateway_id,
            self._epoch,
        )

    def tick(self) -> GatewayVMTickResult:
        """Execute a single gateway epoch (public for testing)."""
        with self._epoch_lock:
            self._epoch += 1
        result = GatewayVMTickResult(epoch=self._epoch)

        with self._data_lock:
            # --- 1. Process retry queue ---
            remaining_retries: list[tuple[GatewayAttestation, BridgeEvent, int]] = []
            for attestation, event, attempts in self._retry_queue:
                if attempts >= self._config.max_retries:
                    logger.warning(
                        "Gateway: event %s exceeded max retries (%d), dropping",
                        event.event_id[:32],
                        self._config.max_retries,
                    )
                    result.anchor_failures += 1
                    continue
                result.retries_attempted += 1
                if not self._try_anchor(attestation, event, result):
                    remaining_retries.append((attestation, event, attempts + 1))
            self._retry_queue = remaining_retries

            # --- 2. Poll for new events ---
            try:
                events = self._listener.poll()
            except Exception as exc:
                logger.error("Gateway: poll failed: %s", exc)
                result.error = f"poll failed: {exc}"
                return result

            result.events_observed = len(events)
            if self._metrics:
                self._metrics["etp_gateway_events_observed"].inc(len(events))

            # --- 3. Validate and process each event ---
            for event in events:
                ok, reason = self._validator.validate(event)
                if not ok:
                    result.events_rejected += 1
                    if self._metrics:
                        self._metrics["etp_gateway_events_rejected"].inc(
                            labels={"reason": reason.split(":")[0]}
                        )
                    logger.info("Gateway: rejected event %s: %s", event.tx_hash[:16], reason)
                    continue

                # --- 4. Create attestation ---
                attestation = self._writer.create_attestation(event)

                # --- 5. Anchor to devnet ---
                if self._try_anchor(attestation, event, result):
                    result.events_accepted += 1
                    if self._metrics:
                        self._metrics["etp_gateway_events_accepted"].inc()

        return result

    @property
    def running(self) -> bool:
        return self._running

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def retry_queue_size(self) -> int:
        with self._data_lock:
            return len(self._retry_queue)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Periodic loop: tick at configured interval."""
        while self._running:
            try:
                self.tick()
            except Exception:
                logger.exception("Gateway: error in epoch %d", self._epoch)
            if self._stop_event.wait(timeout=self._config.poll_interval_seconds):
                break

    def _try_anchor(
        self,
        attestation: GatewayAttestation,
        event: BridgeEvent,
        result: GatewayVMTickResult,
    ) -> bool:
        """Attempt to anchor an attestation. Returns True on success."""
        try:
            self._anchor_fn(attestation)
        except Exception as exc:
            logger.warning(
                "Gateway: anchor failed for %s: %s", event.tx_hash[:16], exc
            )
            result.anchor_failures += 1
            self._retry_queue.append((attestation, event, 1))
            return False

        # Mark as processed in replay DB
        self._replay_db.mark_processed(
            event.event_id,
            tx_hash=event.tx_hash,
            block_number=event.block_number,
        )
        return True
