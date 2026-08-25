"""
Tests for the gateway inference router (src/ltp/gateway/routers/inference.py).

Covers:
  - /inference/v1/models listing with prices
  - /inference/v1/chat/completions happy path: completion + usage +
    billing block, revenue lands in the market/ledger
  - 503 when the market or backend is not wired
  - 400 on malformed bodies, 404 on unlisted model
  - 502 when the model backend raises
  - Deterministic request digest (same body -> same digest)
  - /inference/v1/stats aggregates
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


@pytest.fixture
def client(market):
    app = create_app(GatewayConfig(jwt_enabled=False))
    app.state.inference_market = market
    app.state.inference_backend = fake_backend
    app.state.inference_node_id = "gpu-node-1"
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
        response = client.post("/inference/v1/chat/completions", json=BODY)
        assert response.status_code == 200
        payload = response.json()
        assert payload["choices"][0]["message"]["content"].startswith("echo:")
        usage = payload["usage"]
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
        billing = payload["billing"]
        assert billing["settled_micro"] >= 1
        assert len(billing["request_digest"]) == 64
        assert market.settled_count == 1
        assert market.revenue_micro() == billing["settled_micro"]
        # The serving node accrued the operator share as a claim.
        assert (
            market.ledger.account("gpu-node-1").earned_claim_micro
            == (billing["provider_claim_micro"])
        )
        assert market.ledger.check_solvency()

    def test_two_requests_distinct_ids_same_request_digest(self, client):
        first = client.post("/inference/v1/chat/completions", json=BODY).json()
        second = client.post("/inference/v1/chat/completions", json=BODY).json()
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

    def test_502_when_backend_raises(self, market):
        def broken_backend(model_id, messages):
            raise RuntimeError("model runtime down")

        app = create_app(GatewayConfig(jwt_enabled=False))
        app.state.inference_market = market
        app.state.inference_backend = broken_backend
        response = TestClient(app).post("/inference/v1/chat/completions", json=BODY)
        assert response.status_code == 502
        assert market.settled_count == 0


class TestStats:
    def test_stats_aggregate(self, client, market):
        client.post("/inference/v1/chat/completions", json=BODY)
        response = client.get("/inference/v1/stats")
        assert response.status_code == 200
        stats = response.json()
        assert stats["settled_requests"] == 1
        assert stats["revenue_micro"] == market.revenue_micro()
        assert stats["per_model"][0]["id"] == "suwappu-1"
