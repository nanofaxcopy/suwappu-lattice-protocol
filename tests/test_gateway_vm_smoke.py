"""
Post-deploy smoke tests for a running ETP Gateway VM.

Run these after starting the gateway with:

    bash deploy/run_gateway.sh

Then execute:

    pytest tests/test_gateway_vm_smoke.py -v -m smoke

All three tests are skipped automatically when the gateway is not reachable,
so this file is safe to include in the normal CI suite without a live service.
"""

from __future__ import annotations

import os

import httpx
import pytest

GATEWAY_URL: str = os.environ.get("ETP_GATEWAY_URL", "http://localhost:8000")


def _requires_live_gateway() -> None:
    """Skip the test if the gateway is not reachable."""
    try:
        with httpx.Client(base_url=GATEWAY_URL, timeout=10) as client:
            client.get("/gateway/health")
    except (httpx.ConnectError, httpx.TimeoutException):
        pytest.skip("gateway not reachable")


@pytest.mark.smoke
class TestGatewaySmoke:
    """Smoke tests that hit a live Gateway VM over HTTP."""

    def setup_method(self, _method) -> None:
        _requires_live_gateway()
        self.client = httpx.Client(base_url=GATEWAY_URL, timeout=10)

    def teardown_method(self, _method) -> None:
        self.client.close()

    # ------------------------------------------------------------------
    # 1. Health probe
    # ------------------------------------------------------------------

    def test_health_returns_ok(self) -> None:
        """GET /gateway/health returns 200 with expected body."""
        resp = self.client.get("/gateway/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["checks"]["service"] == "running"

    # ------------------------------------------------------------------
    # 2. Operational status
    # ------------------------------------------------------------------

    def test_status_returns_chain_ids(self) -> None:
        """GET /gateway/status returns 200 with correct chain IDs and fields."""
        resp = self.client.get("/gateway/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source_chain_id"] == 84532
        assert data["dest_chain_id"] == 103115120
        assert data["status"] in ("active", "degraded")
        assert "gateway_id" in data
        assert "epoch" in data

    # ------------------------------------------------------------------
    # 3. Events endpoint reachable
    # ------------------------------------------------------------------

    def test_events_endpoint_reachable(self) -> None:
        """GET /gateway/events returns 200 with a count field."""
        resp = self.client.get("/gateway/events")
        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
