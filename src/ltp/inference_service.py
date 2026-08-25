"""
Inference service — the whole marketplace, composed and runnable.

Everything the inference product needs, wired in one call: the
solvency-enforced ``StablecoinLedger``, the ``InferenceMarket`` with
its Merkle-log receipt verifier, the ``ReceiptCommitmentLog`` signing
tree heads with a real ML-DSA-65 key, the ``DepositWatcher`` crediting
bridged stablecoins, the epoch settlement engine paying providers, and
the FastAPI gateway serving the OpenAI-shaped API — one object, one
``start()``.

    service = build_inference_service(InferenceServiceConfig(port=8080))
    service.start()
    # POST {service.url}/inference/v1/chat/completions ...
    service.settle_epoch()      # pay the providers
    service.stop()

The model backend is the deployment's own runtime. Two adapters ship:

- ``echo_backend()`` — deterministic dev backend, no model needed.
- ``openai_compatible_backend(base_url)`` — fronts a self-hosted
  OpenAI-compatible server (vLLM, TGI, llama.cpp server, …) over HTTP.
  This is how the network's own weights plug in; it is not a
  third-party inference API client.

Configuration reads from the environment (``SUWAPPU_INFER_*``) so the
service boots identically under Docker, systemd, or a shell:

    SUWAPPU_INFER_PORT=8080
    SUWAPPU_INFER_MODEL_ID=suwappu-1
    SUWAPPU_INFER_INPUT_MICRO_PER_MTOK=250000
    SUWAPPU_INFER_OUTPUT_MICRO_PER_MTOK=1000000
    SUWAPPU_INFER_MIN_BALANCE_MICRO=100000
    SUWAPPU_INFER_NODE_ID=gpu-node-1
    SUWAPPU_INFER_BACKEND_URL=http://localhost:8000   # else echo backend

Like the rest of the inference stack, this module is NOT re-exported
from ``ltp.__init__`` (private per ``docs/STABILITY_PROMISES.md``).
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .bridge_deposits import DepositWatcher
from .incentives import IncentiveConfig, StablecoinLedger, StableNodeIncentive
from .inference import InferenceMarket, InferencePricing, ReceiptCommitmentLog

__all__ = [
    "InferenceService",
    "InferenceServiceConfig",
    "build_inference_service",
    "echo_backend",
    "openai_compatible_backend",
]

Backend = Callable[[str, list], tuple[str, int, int]]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class InferenceServiceConfig:
    """Service configuration; ``from_env`` reads ``SUWAPPU_INFER_*``."""

    host: str = "0.0.0.0"
    port: int = 8080
    node_id: str = "gateway"
    model_id: str = "suwappu-1"
    input_micro_per_mtok: int = 250_000  # $0.25 / MTok in
    output_micro_per_mtok: int = 1_000_000  # $1.00 / MTok out
    min_balance_micro: int = 100_000  # $0.10 serve floor
    min_confirmations: int = 6
    jwt_enabled: bool = False
    backend_url: str = ""  # empty -> echo backend
    # Chain deposit polling (all three required to activate):
    bridge_rpc_url: str = ""
    bridge_emitter: str = ""
    bridge_deposit_recipient: str = ""
    bridge_start_block: int = 0
    bridge_amount_divisor: int = 1
    deposit_poll_seconds: float = 15.0

    @classmethod
    def from_env(cls, prefix: str = "SUWAPPU_INFER_") -> "InferenceServiceConfig":
        """Build a config from environment variables (missing = default)."""
        defaults = cls()

        def _int(name: str, default: int) -> int:
            raw = os.environ.get(prefix + name, "")
            return int(raw) if raw else default

        def _str(name: str, default: str) -> str:
            return os.environ.get(prefix + name, default)

        return cls(
            host=_str("HOST", defaults.host),
            port=_int("PORT", defaults.port),
            node_id=_str("NODE_ID", defaults.node_id),
            model_id=_str("MODEL_ID", defaults.model_id),
            input_micro_per_mtok=_int("INPUT_MICRO_PER_MTOK", defaults.input_micro_per_mtok),
            output_micro_per_mtok=_int("OUTPUT_MICRO_PER_MTOK", defaults.output_micro_per_mtok),
            min_balance_micro=_int("MIN_BALANCE_MICRO", defaults.min_balance_micro),
            min_confirmations=_int("MIN_CONFIRMATIONS", defaults.min_confirmations),
            jwt_enabled=_str("JWT_ENABLED", "") == "1",
            backend_url=_str("BACKEND_URL", defaults.backend_url),
            bridge_rpc_url=_str("BRIDGE_RPC_URL", defaults.bridge_rpc_url),
            bridge_emitter=_str("BRIDGE_EMITTER", defaults.bridge_emitter),
            bridge_deposit_recipient=_str(
                "BRIDGE_DEPOSIT_RECIPIENT", defaults.bridge_deposit_recipient
            ),
            bridge_start_block=_int("BRIDGE_START_BLOCK", defaults.bridge_start_block),
            bridge_amount_divisor=_int("BRIDGE_AMOUNT_DIVISOR", defaults.bridge_amount_divisor),
            deposit_poll_seconds=float(
                _str("DEPOSIT_POLL_SECONDS", str(defaults.deposit_poll_seconds))
            ),
        )


# ---------------------------------------------------------------------------
# Model backends
# ---------------------------------------------------------------------------


def echo_backend() -> Backend:
    """Deterministic dev backend: echoes the last message, meters by words."""

    def backend(model_id: str, messages: list) -> tuple[str, int, int]:
        prompt_tokens = sum(len(str(message.get("content", "")).split()) for message in messages)
        text = f"echo({model_id}): {messages[-1].get('content', '')}"
        return text, prompt_tokens, len(text.split())

    return backend


def openai_compatible_backend(
    base_url: str, api_key: str | None = None, timeout_seconds: float = 120.0
) -> Backend:
    """Front a self-hosted OpenAI-compatible model server over HTTP.

    Points at the deployment's own runtime (vLLM, TGI, llama.cpp
    server, …) at ``{base_url}/v1/chat/completions``. Token counts come
    from the runtime's ``usage`` block — the same counts the customer
    is billed on. Stdlib-only (urllib), so it adds no dependency.
    """
    endpoint = base_url.rstrip("/") + "/v1/chat/completions"

    def backend(model_id: str, messages: list) -> tuple[str, int, int]:
        payload = json.dumps({"model": model_id, "messages": messages}).encode("utf-8")
        request = urllib.request.Request(
            endpoint, data=payload, headers={"Content-Type": "application/json"}
        )
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"]
        usage = body.get("usage", {})
        return (
            text,
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )

    return backend


# ---------------------------------------------------------------------------
# The composed service
# ---------------------------------------------------------------------------


class InferenceService:
    """A fully wired inference marketplace node.

    Holds every subsystem by name so operations code (and tests) can
    reach each one: ``ledger``, ``market``, ``receipt_log``,
    ``deposits``, ``incentive``, ``gateway``, ``keypair``.
    """

    def __init__(
        self,
        config: InferenceServiceConfig,
        ledger,
        market,
        receipt_log,
        deposits,
        incentive,
        gateway,
        keypair,
        deposit_source=None,
    ) -> None:
        self.config = config
        self.ledger = ledger
        self.market = market
        self.receipt_log = receipt_log
        self.deposits = deposits
        self.incentive = incentive
        self.gateway = gateway
        self.keypair = keypair
        self.deposit_source = deposit_source
        self._epoch = 0
        self._poll_stop = None
        self._poll_thread = None

    # --- Lifecycle ---

    def start(self) -> None:
        """Start the HTTP gateway and, when configured, deposit polling."""
        self.gateway.start()
        if self.deposit_source is not None:
            self.start_deposit_polling()

    def stop(self) -> None:
        """Stop deposit polling and the HTTP gateway."""
        self.stop_deposit_polling()
        self.gateway.stop()

    def poll_deposits_once(self):
        """One synchronous deposit poll; returns the credits applied."""
        if self.deposit_source is None:
            return []
        return self.deposits.poll_once(self.deposit_source)

    def start_deposit_polling(self) -> None:
        """Poll the chain for deposits on ``deposit_poll_seconds`` cadence.

        A failed poll (RPC hiccup) is logged and retried on the next
        tick — the watcher's idempotency makes missed-then-replayed
        windows harmless.
        """
        import logging
        import threading

        if self._poll_thread is not None:
            return
        logger = logging.getLogger(__name__)
        self._poll_stop = threading.Event()
        stop = self._poll_stop

        def _loop() -> None:
            while not stop.is_set():
                try:
                    credited = self.poll_deposits_once()
                    if credited:
                        logger.info("credited %d bridge deposits", len(credited))
                except Exception:
                    logger.exception("deposit poll failed; retrying next tick")
                stop.wait(self.config.deposit_poll_seconds)

        self._poll_thread = threading.Thread(target=_loop, daemon=True, name="suwappu-deposit-poll")
        self._poll_thread.start()

    def stop_deposit_polling(self) -> None:
        """Stop the deposit polling thread, if running."""
        if self._poll_stop is not None:
            self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5.0)
        self._poll_stop = None
        self._poll_thread = None

    @property
    def url(self) -> str:
        """Base URL of the running gateway."""
        return self.gateway.url

    # --- Operations ---

    def settle_epoch(self):
        """Pay accrued provider claims from the operator pool.

        Solvency-clamped as always; returns the ``EpochPayoutSnapshot``.
        """
        self._epoch += 1
        return self.incentive.settle_epoch(self._epoch)


def build_inference_service(
    config: InferenceServiceConfig | None = None,
    backend: Backend | None = None,
    keypair=None,
    deposit_source=None,
) -> InferenceService:
    """Compose every subsystem into a runnable service.

    ``backend`` defaults from ``config.backend_url`` (set → OpenAI-
    compatible adapter, empty → echo). ``keypair`` (ML-DSA-65) signs
    the receipt log's tree heads and the gateway's JWTs; generated
    fresh when not supplied — supply the node's persistent keypair in
    production so STHs stay attributable across restarts.
    """
    from .gateway.app import GatewayConfig, GatewayServer
    from .keypair import KeyPair
    from .merkle_log import MerkleLog

    config = config or InferenceServiceConfig()
    keypair = keypair or KeyPair.generate("inference-service")
    if backend is None:
        backend = (
            openai_compatible_backend(config.backend_url) if config.backend_url else echo_backend()
        )

    ledger = StablecoinLedger(IncentiveConfig())
    # LTP-A-032 Phase 4d: pass the KeyPair itself so STH signing routes
    # through KeyPair.sign — HSM-backed keypairs (the implicit default)
    # stay sentinel-only; software keypairs behave identically.
    receipt_log = ReceiptCommitmentLog(MerkleLog(keypair.vk, keypair))
    market = InferenceMarket(
        ledger,
        receipt_verifier=receipt_log.verifier(),
        min_balance_to_serve_micro=config.min_balance_micro,
    )
    market.register_model(
        InferencePricing(
            model_id=config.model_id,
            input_micro_per_mtok=config.input_micro_per_mtok,
            output_micro_per_mtok=config.output_micro_per_mtok,
        )
    )
    deposits = DepositWatcher(ledger, min_confirmations=config.min_confirmations)
    if deposit_source is None and (
        config.bridge_rpc_url and config.bridge_emitter and config.bridge_deposit_recipient
    ):
        from .bridge_deposits import BridgeEmitterDepositSource

        deposit_source = BridgeEmitterDepositSource.from_rpc_url(
            config.bridge_rpc_url,
            emitter_address=config.bridge_emitter,
            deposit_recipient=config.bridge_deposit_recipient,
            start_block=config.bridge_start_block,
            amount_divisor=config.bridge_amount_divisor,
        )
    incentive = StableNodeIncentive(ledger)
    gateway = GatewayServer(
        config=GatewayConfig(host=config.host, port=config.port, jwt_enabled=config.jwt_enabled),
        keypair=keypair,
        inference_market=market,
        inference_backend=backend,
        inference_node_id=config.node_id,
        inference_receipt_log=receipt_log,
    )
    return InferenceService(
        config=config,
        ledger=ledger,
        market=market,
        receipt_log=receipt_log,
        deposits=deposits,
        incentive=incentive,
        gateway=gateway,
        keypair=keypair,
        deposit_source=deposit_source,
    )
