"""
Tests for the composed inference service and the bridge deposit watcher.

Covers:
  - DepositWatcher: address binding (rebind refused, unbind), event
    validation, confirmation gating, idempotent crediting by tx hash,
    unattributed quarantine + late binding, solvency intact
  - InferenceServiceConfig.from_env parsing
  - Backend adapters: echo determinism; openai_compatible_backend
    against a local stub HTTP server (token counts from usage)
  - build_inference_service under the implicit-HSM default: the
    receipt log signs STHs through KeyPair.sign (LTP-A-032)
  - The full loop over REAL HTTP (uvicorn, ephemeral port): deposit ->
    completion -> committed bill -> audit bundle verifies -> epoch
    payout -> ledger solvency
"""

from __future__ import annotations

import http.server
import json
import threading
import urllib.request

import pytest

from src.ltp.bridge_deposits import DepositError, DepositEvent, DepositWatcher
from src.ltp.incentives import IncentiveConfig, StablecoinLedger
from src.ltp.inference_service import (
    InferenceServiceConfig,
    build_inference_service,
    echo_backend,
    openai_compatible_backend,
)

ADDRESS = "0x" + "ab" * 20
TX = "0x" + "11" * 32


def make_event(tx_hash=TX, sender=ADDRESS, amount=1_000_000, confirmations=12):
    return DepositEvent(
        tx_hash=tx_hash, sender=sender, amount_micro=amount, confirmations=confirmations
    )


# ---------------------------------------------------------------------------
# Deposit watcher
# ---------------------------------------------------------------------------


class TestDepositWatcher:
    def test_confirmed_bound_deposit_credits_once(self):
        ledger = StablecoinLedger(IncentiveConfig())
        watcher = DepositWatcher(ledger, min_confirmations=6)
        watcher.bind_address(ADDRESS, "alice")
        credited = watcher.process([make_event()])
        assert credited[0].customer_id == "alice"
        assert credited[0].balance_after_micro == 1_000_000
        # Replays are free: same event, no double credit.
        assert watcher.process([make_event()]) == []
        assert ledger.customer_balance("alice") == 1_000_000
        assert watcher.is_credited(TX)
        assert ledger.check_solvency()

    def test_under_confirmed_waits_then_credits(self):
        ledger = StablecoinLedger(IncentiveConfig())
        watcher = DepositWatcher(ledger, min_confirmations=6)
        watcher.bind_address(ADDRESS, "alice")
        assert watcher.process([make_event(confirmations=3)]) == []
        assert ledger.customer_balance("alice") == 0
        # Same tx, now buried deep enough: credits.
        credited = watcher.process([make_event(confirmations=6)])
        assert credited[0].amount_micro == 1_000_000

    def test_unattributed_quarantined_then_credited_after_binding(self):
        ledger = StablecoinLedger(IncentiveConfig())
        watcher = DepositWatcher(ledger, min_confirmations=1)
        assert watcher.process([make_event()]) == []
        assert len(watcher.unattributed()) == 1
        assert ledger.customer_balance("alice") == 0
        # Operations resolves the binding; the next poll credits it.
        watcher.bind_address(ADDRESS, "alice")
        credited = watcher.process([make_event()])
        assert credited[0].customer_id == "alice"
        assert watcher.unattributed() == []

    def test_rebind_to_different_customer_refused(self):
        watcher = DepositWatcher(StablecoinLedger(IncentiveConfig()))
        watcher.bind_address(ADDRESS, "alice")
        watcher.bind_address(ADDRESS, "alice")  # idempotent re-bind is fine
        with pytest.raises(DepositError):
            watcher.bind_address(ADDRESS, "mallory")
        watcher.unbind_address(ADDRESS)
        watcher.bind_address(ADDRESS, "mallory")  # explicit unbind first

    def test_malformed_events_rejected(self):
        with pytest.raises(DepositError):
            make_event(tx_hash="0x123")
        with pytest.raises(DepositError):
            make_event(sender="not-an-address")
        with pytest.raises(DepositError):
            make_event(amount=-1)

    def test_case_normalization(self):
        ledger = StablecoinLedger(IncentiveConfig())
        watcher = DepositWatcher(ledger, min_confirmations=1)
        watcher.bind_address(ADDRESS.upper().replace("0X", "0x"), "alice")
        credited = watcher.process([make_event()])
        assert credited[0].customer_id == "alice"


# ---------------------------------------------------------------------------
# Config + backends
# ---------------------------------------------------------------------------


class TestConfigAndBackends:
    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("SUWAPPU_INFER_PORT", "9123")
        monkeypatch.setenv("SUWAPPU_INFER_MODEL_ID", "suwappu-2")
        monkeypatch.setenv("SUWAPPU_INFER_MIN_BALANCE_MICRO", "5")
        monkeypatch.setenv("SUWAPPU_INFER_JWT_ENABLED", "1")
        config = InferenceServiceConfig.from_env()
        assert config.port == 9123
        assert config.model_id == "suwappu-2"
        assert config.min_balance_micro == 5
        assert config.jwt_enabled is True
        # Unset vars keep defaults.
        assert config.input_micro_per_mtok == 250_000

    def test_echo_backend_deterministic(self):
        backend = echo_backend()
        first = backend("m", [{"role": "user", "content": "one two three"}])
        second = backend("m", [{"role": "user", "content": "one two three"}])
        assert first == second
        assert first[1] == 3  # prompt words metered

    def test_openai_compatible_backend_against_stub(self):
        class Stub(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert body["model"] == "suwappu-1"
                response = json.dumps(
                    {
                        "choices": [{"message": {"content": "from-the-runtime"}}],
                        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Stub)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            backend = openai_compatible_backend(f"http://127.0.0.1:{server.server_address[1]}")
            text, prompt_tokens, completion_tokens = backend(
                "suwappu-1", [{"role": "user", "content": "hi"}]
            )
            assert text == "from-the-runtime"
            assert (prompt_tokens, completion_tokens) == (7, 3)
        finally:
            server.shutdown()


# ---------------------------------------------------------------------------
# The full loop over real HTTP
# ---------------------------------------------------------------------------


def _http_json(url, payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers or {})
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


class TestImplicitHsmPosture:
    def test_receipt_log_signs_through_hsm_backed_keypair(self, monkeypatch):
        """Pin the production posture the test conftest normally disables.

        Outside pytest, LTP_KEYPAIR_IMPLICIT_HSM defaults on and
        KeyPair.sk is a sentinel — raw-bytes signing paths break there.
        This test forces the HSM mode and proves the receipt log still
        signs valid STHs (via KeyPair.sign, LTP-A-032 Phase 4d).
        """
        monkeypatch.setenv("LTP_KEYPAIR_IMPLICIT_HSM", "1")
        from src.ltp.inference import receipt_canonical_bytes

        service = build_inference_service(InferenceServiceConfig(port=0))
        assert service.keypair.is_hsm_backed
        from src.ltp.inference import InferenceReceipt, receipt_digest

        receipt = InferenceReceipt(
            request_id="req-hsm",
            node_id="gpu-1",
            model_id="suwappu-1",
            input_tokens=10,
            output_tokens=5,
            request_digest=receipt_digest(b"request"),
            response_digest=receipt_digest(b"response"),
        )
        service.receipt_log.commit(receipt)
        sth = service.receipt_log.latest_sth
        assert sth.verify()
        assert service.receipt_log.is_committed(receipt)
        bundle = service.receipt_log.proof("req-hsm")
        assert bytes.fromhex(bundle["record"]) == receipt_canonical_bytes(receipt)


class TestFullLoopOverHTTP:
    def test_deposit_serve_audit_payout_solvency(self):
        service = build_inference_service(
            InferenceServiceConfig(host="127.0.0.1", port=0, node_id="gpu-node-1")
        )
        service.start()
        try:
            base = service.url
            headers = {"x-suwappu-customer": "cust-alice"}

            # Bridge deposit funds the customer.
            service.deposits.bind_address(ADDRESS, "cust-alice")
            service.deposits.process([make_event(amount=5_000_000)])
            balance = _http_json(f"{base}/inference/v1/balance", headers=headers)
            assert balance["balance_micro"] == 5_000_000

            # Buy a completion; the bill is committed.
            completion = _http_json(
                f"{base}/inference/v1/chat/completions",
                payload={
                    "model": "suwappu-1",
                    "messages": [{"role": "user", "content": "hello suwappu"}],
                },
                headers=headers,
            )
            billing = completion["billing"]
            assert billing["settled_micro"] >= 1
            assert billing["commitment"]["leaf_index"] == 0

            # The audit bundle verifies: ML-DSA STH + inclusion proof.
            from src.ltp.merkle_log.proof import InclusionProof
            from src.ltp.merkle_log.sth import SignedTreeHead

            bundle = _http_json(
                f"{base}/inference/v1/receipts/{billing['request_id']}",
                headers=headers,
            )
            sth = SignedTreeHead(
                sequence=bundle["sth"]["sequence"],
                tree_size=bundle["sth"]["tree_size"],
                timestamp=bundle["sth"]["timestamp"],
                root_hash=bytes.fromhex(bundle["sth"]["root_hash"]),
                operator_vk=bytes.fromhex(bundle["sth"]["operator_vk"]),
                signature=bytes.fromhex(bundle["sth"]["signature"]),
            )
            assert sth.verify()
            assert sth.operator_vk == service.keypair.vk
            proof = InclusionProof(
                leaf_index=bundle["leaf_index"],
                tree_size=bundle["tree_size"],
                audit_path=[bytes.fromhex(node) for node in bundle["audit_path"]],
                root_hash=bytes.fromhex(bundle["root_hash"]),
            )
            assert proof.verify(bytes.fromhex(bundle["record"]), sth.root_hash)

            # Epoch settlement pays the provider its operator share.
            snapshot = service.settle_epoch()
            assert snapshot.payouts["gpu-node-1"] > 0
            assert snapshot.fully_funded

            # Every micro accounted for.
            assert service.ledger.check_solvency()
        finally:
            service.stop()
