"""
Tests for the gateway inference router (src/ltp/gateway/routers/inference.py).

Covers:
  - /inference/v1/models listing with prices
  - /inference/v1/chat/completions happy path: completion + usage +
    billing block, prepaid balance debited, revenue lands in the ledger
  - 402 before serving when the balance is under the serve floor
  - 402 after metering when the balance can't cover the amount due
    (completion withheld, nothing debited)
  - Customer resolution via the x-suwappu-customer header fallback
  - /inference/v1/balance read
  - 503 when the market or backend is not wired
  - 400 on malformed bodies, 404 on unlisted model
  - 502 when the model backend raises
  - Deterministic request digest (same body -> same digest)
  - /inference/v1/stats aggregates
  - Receipt commitment wiring: commitment block in billing, market
    verifier fed by the log, GET /inference/v1/receipts/{id} audit
    bundle verifying end to end, 404/503 paths
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.ltp.gateway.app import GatewayConfig, create_app
from src.ltp.incentives import IncentiveConfig, StablecoinLedger
from src.ltp.inference import InferenceMarket, InferencePricing


def fake_backend(model_id, messages):
    # Deterministic toy backend: echoes, meters by word count.
    prompt_tokens = sum(len(str(m.get("content", "")).split()) for m in messages)
    text = f"echo:{messages[-1].get('content', '')}"
    return text, prompt_tokens, len(text.split())


@pytest.fixture
def market():
    m = InferenceMarket(StablecoinLedger(IncentiveConfig()))
    m.register_model(
        InferencePricing(
            model_id="suwappu-1",
            input_micro_per_mtok=250_000,
            output_micro_per_mtok=1_000_000,
        )
    )
    return m


CUSTOMER = "cust-alice"
HEADERS = {"x-suwappu-customer": CUSTOMER}


@pytest.fixture
def client(market):
    app = create_app(GatewayConfig(jwt_enabled=False))
    app.state.inference_market = market
    app.state.inference_backend = fake_backend
    app.state.inference_node_id = "gpu-node-1"
    # Fund the test customer well above the serve floor.
    market.ledger.customer_deposit(CUSTOMER, 10_000_000)  # $10
    return TestClient(app)


BODY = {"model": "suwappu-1", "messages": [{"role": "user", "content": "hello there"}]}


class TestModels:
    def test_lists_models_with_prices(self, client):
        response = client.get("/inference/v1/models")
        assert response.status_code == 200
        models = response.json()["models"]
        assert models == [
            {
                "id": "suwappu-1",
                "input_micro_per_mtok": 250_000,
                "output_micro_per_mtok": 1_000_000,
            }
        ]

    def test_503_without_market(self):
        app = create_app(GatewayConfig(jwt_enabled=False))
        response = TestClient(app).get("/inference/v1/models")
        assert response.status_code == 503


class TestCompletions:
    def test_happy_path_bills_and_returns_completion(self, client, market):
        response = client.post("/inference/v1/chat/completions", json=BODY, headers=HEADERS)
        assert response.status_code == 200
        payload = response.json()
        assert payload["choices"][0]["message"]["content"].startswith("echo:")
        usage = payload["usage"]
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
        billing = payload["billing"]
        assert billing["settled_micro"] >= 1
        assert billing["customer_id"] == CUSTOMER
        assert len(billing["request_digest"]) == 64
        assert market.settled_count == 1
        assert market.revenue_micro() == billing["settled_micro"]
        # Prepaid debit: balance went down by exactly the settled amount.
        assert billing["balance_after_micro"] == 10_000_000 - billing["settled_micro"]
        assert market.ledger.customer_balance(CUSTOMER) == billing["balance_after_micro"]
        # The serving node accrued the operator share as a claim.
        assert (
            market.ledger.account("gpu-node-1").earned_claim_micro
            == billing["provider_claim_micro"]
        )
        assert market.ledger.check_solvency()

    def test_402_below_serve_floor_before_backend_runs(self, client, market):
        # An unfunded customer is refused before any compute happens.
        response = client.post(
            "/inference/v1/chat/completions",
            json=BODY,
            headers={"x-suwappu-customer": "cust-broke"},
        )
        assert response.status_code == 402
        payload = response.json()
        assert payload["balance_micro"] == 0
        assert payload["minimum_micro"] == market.min_balance_to_serve_micro
        assert market.settled_count == 0

    def test_402_when_metered_usage_exceeds_balance(self, market):
        # Balance clears the floor but the metered bill exceeds it: the
        # completion is withheld and nothing is debited.
        app = create_app(GatewayConfig(jwt_enabled=False))
        app.state.inference_market = market
        app.state.inference_node_id = "gpu-node-1"
        # Backend reports a huge output; bill far exceeds the balance.
        app.state.inference_backend = lambda m, msgs: ("big", 10, 10_000_000_000)
        market.ledger.customer_deposit("cust-small", market.min_balance_to_serve_micro)
        response = TestClient(app).post(
            "/inference/v1/chat/completions",
            json=BODY,
            headers={"x-suwappu-customer": "cust-small"},
        )
        assert response.status_code == 402
        payload = response.json()
        assert payload["due_micro"] > payload["balance_micro"]
        assert market.settled_count == 0
        assert market.ledger.customer_balance("cust-small") == market.min_balance_to_serve_micro

    def test_two_requests_distinct_ids_same_request_digest(self, client):
        first = client.post("/inference/v1/chat/completions", json=BODY, headers=HEADERS).json()
        second = client.post("/inference/v1/chat/completions", json=BODY, headers=HEADERS).json()
        assert first["billing"]["request_id"] != second["billing"]["request_id"]
        assert first["billing"]["request_digest"] == second["billing"]["request_digest"]

    def test_503_without_backend(self, market):
        app = create_app(GatewayConfig(jwt_enabled=False))
        app.state.inference_market = market
        response = TestClient(app).post("/inference/v1/chat/completions", json=BODY)
        assert response.status_code == 503

    def test_400_on_missing_fields(self, client):
        assert (
            client.post("/inference/v1/chat/completions", json={"messages": [{}]}).status_code
            == 400
        )
        assert (
            client.post("/inference/v1/chat/completions", json={"model": "suwappu-1"}).status_code
            == 400
        )

    def test_404_on_unlisted_model(self, client):
        body = dict(BODY, model="missing-model")
        assert client.post("/inference/v1/chat/completions", json=body).status_code == 404

    def test_unlisted_model_is_not_reflected_in_the_error_body(self, client):
        """The 404 must not echo the request's model name back.

        CodeQL flagged the request-derived model id reaching a log line
        (py/log-injection); the same value was also being interpolated
        into this error body. Both are now cut off at the pricing
        lookup, and this pins the response half.
        """
        marker = "canary-\n\rINJECTED-LOG-LINE"
        body = dict(BODY, model=marker)
        response = client.post("/inference/v1/chat/completions", json=body)
        assert response.status_code == 404
        assert marker not in response.text
        assert "INJECTED" not in response.text

    def test_backend_failure_logs_only_the_listed_model_id(self, market, caplog):
        """A failing backend must log the operator's id, never the caller's.

        Log records are the artifact an operator reads during an
        incident; a model name carrying newlines could otherwise forge
        entries in it. The request never reaches the backend unless it
        matched a listing, so the listed id is the only safe thing to
        name — this asserts that is what gets logged.
        """
        import logging

        def broken_backend(model_id, messages):
            raise RuntimeError("model runtime down")

        app = create_app(GatewayConfig(jwt_enabled=False))
        app.state.inference_market = market
        app.state.inference_backend = broken_backend
        market.ledger.customer_deposit(CUSTOMER, 10_000_000)

        with caplog.at_level(logging.ERROR, logger="ltp.gateway.routers.inference"):
            response = TestClient(app).post(
                "/inference/v1/chat/completions", json=BODY, headers=HEADERS
            )

        assert response.status_code == 502
        messages = [record.getMessage() for record in caplog.records]
        assert "inference backend failed for model suwappu-1" in messages

    def test_502_when_backend_raises(self, market):
        def broken_backend(model_id, messages):
            raise RuntimeError("model runtime down")

        app = create_app(GatewayConfig(jwt_enabled=False))
        app.state.inference_market = market
        app.state.inference_backend = broken_backend
        market.ledger.customer_deposit(CUSTOMER, 10_000_000)
        response = TestClient(app).post(
            "/inference/v1/chat/completions", json=BODY, headers=HEADERS
        )
        assert response.status_code == 502
        assert market.settled_count == 0


class TestBalance:
    def test_balance_read_for_header_customer(self, client, market):
        response = client.get("/inference/v1/balance", headers=HEADERS)
        assert response.status_code == 200
        payload = response.json()
        assert payload["customer_id"] == CUSTOMER
        assert payload["balance_micro"] == 10_000_000
        assert payload["minimum_to_serve_micro"] == market.min_balance_to_serve_micro

    def test_balance_defaults_to_anonymous(self, client):
        response = client.get("/inference/v1/balance")
        assert response.json()["customer_id"] == "anonymous"


class TestStats:
    def test_stats_aggregate(self, client, market):
        client.post("/inference/v1/chat/completions", json=BODY, headers=HEADERS)
        response = client.get("/inference/v1/stats")
        assert response.status_code == 200
        stats = response.json()
        assert stats["settled_requests"] == 1
        assert stats["revenue_micro"] == market.revenue_micro()
        assert stats["per_model"][0]["id"] == "suwappu-1"


class TestReceiptCommitmentWiring:
    def _committed_app(self):
        from src.ltp.inference import ReceiptCommitmentLog
        from src.ltp.keypair import KeyPair
        from src.ltp.merkle_log import MerkleLog

        keypair = KeyPair.generate("gw-receipt-test")
        receipt_log = ReceiptCommitmentLog(MerkleLog(keypair.vk, keypair))
        ledger = StablecoinLedger(IncentiveConfig())
        market = InferenceMarket(ledger, receipt_verifier=receipt_log.verifier())
        market.register_model(
            InferencePricing(
                model_id="suwappu-1",
                input_micro_per_mtok=250_000,
                output_micro_per_mtok=1_000_000,
            )
        )
        ledger.customer_deposit(CUSTOMER, 10_000_000)
        app = create_app(GatewayConfig(jwt_enabled=False))
        app.state.inference_market = market
        app.state.inference_backend = fake_backend
        app.state.inference_node_id = "gpu-node-1"
        app.state.inference_receipt_log = receipt_log
        return TestClient(app), market, receipt_log

    def test_completion_carries_commitment_and_settles(self):
        client, market, receipt_log = self._committed_app()
        response = client.post("/inference/v1/chat/completions", json=BODY, headers=HEADERS)
        assert response.status_code == 200
        billing = response.json()["billing"]
        commitment = billing["commitment"]
        assert commitment["leaf_index"] == 0
        assert len(commitment["root_hash"]) == 64
        # Settlement went through the log-backed verifier.
        assert market.settled_count == 1
        assert receipt_log.leaf_index(billing["request_id"]) == 0

    def test_receipts_endpoint_returns_verifiable_bundle(self):
        from src.ltp.merkle_log.proof import InclusionProof
        from src.ltp.merkle_log.sth import SignedTreeHead

        client, _market, _log = self._committed_app()
        billing = client.post("/inference/v1/chat/completions", json=BODY, headers=HEADERS).json()[
            "billing"
        ]
        response = client.get(f"/inference/v1/receipts/{billing['request_id']}")
        assert response.status_code == 200
        bundle = response.json()

        sth = SignedTreeHead(
            sequence=bundle["sth"]["sequence"],
            tree_size=bundle["sth"]["tree_size"],
            timestamp=bundle["sth"]["timestamp"],
            root_hash=bytes.fromhex(bundle["sth"]["root_hash"]),
            operator_vk=bytes.fromhex(bundle["sth"]["operator_vk"]),
            signature=bytes.fromhex(bundle["sth"]["signature"]),
        )
        assert sth.verify()
        proof = InclusionProof(
            leaf_index=bundle["leaf_index"],
            tree_size=bundle["tree_size"],
            audit_path=[bytes.fromhex(h) for h in bundle["audit_path"]],
            root_hash=bytes.fromhex(bundle["root_hash"]),
        )
        assert proof.verify(bytes.fromhex(bundle["record"]), sth.root_hash)

    def test_receipts_endpoint_404_unknown(self):
        client, _market, _log = self._committed_app()
        assert client.get("/inference/v1/receipts/nope").status_code == 404

    def test_receipts_endpoint_503_without_log(self, client):
        assert client.get("/inference/v1/receipts/nope").status_code == 503

    def test_no_commitment_block_without_log(self, client):
        billing = client.post("/inference/v1/chat/completions", json=BODY, headers=HEADERS).json()[
            "billing"
        ]
        assert billing["commitment"] is None
