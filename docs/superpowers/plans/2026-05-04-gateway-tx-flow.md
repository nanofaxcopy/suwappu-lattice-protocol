# Gateway Transaction Flow — Implementation Plan (Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the end-to-end transaction flow from bridge contract event to devnet commitment: `DevnetAnchorClient` (extends `AnchorClient` with gateway-specific submission), `AnchorStatusTracker` integration, REST endpoints for gateway monitoring, Grafana dashboard, and bidirectional integration tests proving Base Sepolia → GSX and GSX → Base Sepolia flows.

**Architecture:** The opaque `anchor_fn` callable from Plan 1 is replaced with a real `DevnetAnchorClient` that inherits `AnchorClient`'s rate limiter and circuit breaker. `AnchorStatusTracker` tracks each attestation through PENDING → SUBMITTED → CONFIRMED → FINALIZED. New REST endpoints on the existing `GatewayServer` (FastAPI) expose gateway status, processed events, and event lookups. A Grafana dashboard template visualizes gateway metrics.

**Tech Stack:** Python 3.12+, FastAPI (existing gateway), AnchorClient (existing), AnchorStatusTracker (existing), Prometheus/Grafana (existing observability stack), pytest

**Spec:** `docs/LTP_GATEWAY_VM_PLAN.md` — Phase 2 deliverables (Section: Phase 2: Triggering Transaction Flow)

**Depends on:** Plan 1 (Gateway VM Core) — all 14 tasks complete.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/ltp/gateway_vm/anchor_client.py` | `DevnetAnchorClient` — extends AnchorClient for gateway attestation submission |
| `src/ltp/gateway_vm/tracker.py` | `GatewayTracker` — thin wrapper wiring AnchorStatusTracker to gateway attestations |
| `src/ltp/gateway_vm/routers/__init__.py` | Router package |
| `src/ltp/gateway_vm/routers/status.py` | `GET /gateway/status`, `GET /gateway/health` |
| `src/ltp/gateway_vm/routers/events.py` | `GET /gateway/events`, `GET /gateway/events/{tx_hash}` |
| `deploy/observability/grafana/dashboards/etp-gateway.json` | Grafana dashboard template |
| `deploy/observability/prometheus/alerts.yml` | Add gateway alert rules to existing file |
| `tests/test_gateway_vm_anchor_client.py` | DevnetAnchorClient tests |
| `tests/test_gateway_vm_tracker.py` | GatewayTracker tests |
| `tests/test_gateway_vm_routes.py` | REST endpoint tests |
| `tests/test_gateway_vm_e2e.py` | End-to-end integration: event → validate → attest → anchor → track |
| `tests/test_gateway_vm_bidirectional.py` | Base Sepolia → GSX and GSX → Base Sepolia flow tests |

---

## Task 1: DevnetAnchorClient

**Files:**
- Create: `src/ltp/gateway_vm/anchor_client.py`
- Test: `tests/test_gateway_vm_anchor_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_vm_anchor_client.py`:

```python
"""Tests for DevnetAnchorClient — gateway-specific AnchorClient extension."""

import pytest
from unittest.mock import MagicMock, patch
from src.ltp.keypair import KeyPair


@pytest.fixture(scope="module")
def gateway_kp():
    return KeyPair.generate("anchor-client-test")


def _make_attestation(gateway_kp):
    from src.ltp.gateway_vm.events import BridgeEvent
    from src.ltp.gateway_vm.writer import AttestationWriter

    event = BridgeEvent(
        source_chain_id=84532,
        bridge_contract="0x5083",
        tx_hash="0xabc123",
        block_number=100,
        log_index=0,
        event_name="AnchorCreated",
        sender="0xaa",
        recipient="0xbb",
        payload_hash="sha3-256:ff00",
        amount=100,
        nonce=1,
        timestamp=1700000000.0,
    )
    writer = AttestationWriter(operator_keypair=gateway_kp, dest_chain_id=103115120)
    return writer.create_attestation(event)


class TestDevnetAnchorClientConstruction:
    def test_create_from_config(self):
        from src.ltp.gateway_vm.anchor_client import DevnetAnchorClient
        from src.ltp.gateway_vm.config import GatewayVMConfig

        config = GatewayVMConfig(
            dest_rpc_url="https://rpc.example.com",
            dest_registry_address="0xB29d8BFF4973D1D7bcB10E32112EBB8fdd530bF4",
            dest_chain_id=103115120,
        )
        # Mock the web3 connection to avoid real RPC
        with patch("src.ltp.gateway_vm.anchor_client.AnchorClient.__init__", return_value=None):
            client = DevnetAnchorClient.from_gateway_config(
                config=config,
                operator_private_key="0xdeadbeef",
            )
            assert client is not None

    def test_requires_rpc_url(self):
        from src.ltp.gateway_vm.anchor_client import DevnetAnchorClient
        from src.ltp.gateway_vm.config import GatewayVMConfig

        config = GatewayVMConfig(dest_rpc_url="")
        with pytest.raises(ValueError, match="rpc_url"):
            DevnetAnchorClient.from_gateway_config(
                config=config,
                operator_private_key="0xdeadbeef",
            )


class TestSubmitAttestation:
    def test_submit_calls_anchor(self, gateway_kp):
        from src.ltp.gateway_vm.anchor_client import DevnetAnchorClient

        mock_anchor = MagicMock(return_value="0xtxhash123")
        client = DevnetAnchorClient.__new__(DevnetAnchorClient)
        client._submit_fn = mock_anchor

        attestation = _make_attestation(gateway_kp)
        tx_hash = client.submit_attestation(attestation)

        assert tx_hash == "0xtxhash123"
        mock_anchor.assert_called_once()

    def test_submit_raises_on_circuit_breaker(self, gateway_kp):
        from src.ltp.gateway_vm.anchor_client import DevnetAnchorClient

        client = DevnetAnchorClient.__new__(DevnetAnchorClient)
        client._submit_fn = MagicMock(side_effect=RuntimeError("Circuit breaker OPEN"))

        attestation = _make_attestation(gateway_kp)
        with pytest.raises(RuntimeError, match="Circuit breaker"):
            client.submit_attestation(attestation)


class TestAnchorFnAdapter:
    def test_as_anchor_fn_returns_callable(self, gateway_kp):
        from src.ltp.gateway_vm.anchor_client import DevnetAnchorClient

        client = DevnetAnchorClient.__new__(DevnetAnchorClient)
        client._submit_fn = MagicMock(return_value="0xtx")

        fn = client.as_anchor_fn()
        assert callable(fn)

        attestation = _make_attestation(gateway_kp)
        result = fn(attestation)
        assert result == "0xtx"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_anchor_client.py -v`

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/anchor_client.py`:

```python
"""DevnetAnchorClient — gateway-specific AnchorClient for attestation submission.

Extends the existing AnchorClient with:
  - Gateway config integration (from_gateway_config factory)
  - Attestation-specific submission (converts GatewayAttestation → AnchorSubmission)
  - as_anchor_fn() adapter for Plan 1 service integration

Inherits: rate limiter (TokenBucket), circuit breaker, nonce management,
receipt waiting, and retry logic from AnchorClient.
"""

from __future__ import annotations

from typing import Callable

from ..anchor.client import AnchorClient, AnchorSubmission
from .config import GatewayVMConfig
from .writer import GatewayAttestation


class DevnetAnchorClient:
    """Submits gateway attestations to the GSX devnet LTPAnchorRegistry.

    Wraps AnchorClient rather than inheriting, so it can be used without
    a real RPC connection in tests (inject _submit_fn).
    """

    def __init__(
        self,
        anchor_client: AnchorClient,
    ) -> None:
        self._client = anchor_client
        self._submit_fn: Callable = self._real_submit

    @classmethod
    def from_gateway_config(
        cls,
        config: GatewayVMConfig,
        operator_private_key: str,
    ) -> DevnetAnchorClient:
        """Create from gateway config. Validates required fields."""
        if not config.dest_rpc_url:
            raise ValueError("dest_rpc_url is required for DevnetAnchorClient")
        if not config.dest_registry_address:
            raise ValueError("dest_registry_address is required for DevnetAnchorClient")

        client = AnchorClient(
            rpc_url=config.dest_rpc_url,
            contract_address=config.dest_registry_address,
            private_key=operator_private_key,
            chain_id=config.dest_chain_id,
        )
        return cls(anchor_client=client)

    def submit_attestation(self, attestation: GatewayAttestation) -> str:
        """Submit a gateway attestation to devnet. Returns tx hash."""
        return self._submit_fn(attestation)

    def as_anchor_fn(self) -> Callable[[GatewayAttestation], str]:
        """Return a callable compatible with GatewayVMService.anchor_fn."""
        return self.submit_attestation

    def _real_submit(self, attestation: GatewayAttestation) -> str:
        """Convert attestation to AnchorSubmission and submit via AnchorClient."""
        submission = AnchorSubmission(
            entity_id=attestation.event_id,
            anchor_digest=attestation.digest[:32],
            signer_vk_fingerprint=attestation.signer_vk_fingerprint,
        )
        return self._client.anchor(submission)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_anchor_client.py -v`

Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/gateway_vm/anchor_client.py tests/test_gateway_vm_anchor_client.py
git commit -m "feat(gateway-vm): add DevnetAnchorClient extending AnchorClient for attestation submission"
```

---

## Task 2: Gateway Tracker

**Files:**
- Create: `src/ltp/gateway_vm/tracker.py`
- Test: `tests/test_gateway_vm_tracker.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_vm_tracker.py`:

```python
"""Tests for GatewayTracker — attestation lifecycle tracking."""

import pytest
from src.ltp.keypair import KeyPair


@pytest.fixture(scope="module")
def gateway_kp():
    return KeyPair.generate("tracker-test")


def _make_attestation(gateway_kp, event_id_suffix="aaa"):
    from src.ltp.gateway_vm.events import BridgeEvent
    from src.ltp.gateway_vm.writer import AttestationWriter

    event = BridgeEvent(
        source_chain_id=84532,
        bridge_contract="0x5083",
        tx_hash=f"0x{event_id_suffix}",
        block_number=100,
        log_index=0,
        event_name="AnchorCreated",
        sender="0xaa",
        recipient="0xbb",
        payload_hash="sha3-256:ff00",
        amount=100,
        nonce=1,
        timestamp=1700000000.0,
    )
    writer = AttestationWriter(operator_keypair=gateway_kp, dest_chain_id=103115120)
    return writer.create_attestation(event)


class TestGatewayTracker:
    def test_track_new_attestation(self, gateway_kp):
        from src.ltp.gateway_vm.tracker import GatewayTracker

        tracker = GatewayTracker()
        att = _make_attestation(gateway_kp)
        tracker.mark_pending(att)
        rec = tracker.get(att.event_id)
        assert rec is not None
        assert rec["status"] == "pending"

    def test_lifecycle_pending_to_finalized(self, gateway_kp):
        from src.ltp.gateway_vm.tracker import GatewayTracker

        tracker = GatewayTracker()
        att = _make_attestation(gateway_kp)
        tracker.mark_pending(att)
        tracker.mark_submitted(att.event_id, tx_hash="0xtx1")
        tracker.mark_confirmed(att.event_id, block_number=500, gas_used=21000)
        tracker.mark_finalized(att.event_id)
        rec = tracker.get(att.event_id)
        assert rec["status"] == "finalized"
        assert rec["tx_hash"] == "0xtx1"

    def test_mark_failed(self, gateway_kp):
        from src.ltp.gateway_vm.tracker import GatewayTracker

        tracker = GatewayTracker()
        att = _make_attestation(gateway_kp)
        tracker.mark_pending(att)
        tracker.mark_failed(att.event_id, error="RPC timeout")
        rec = tracker.get(att.event_id)
        assert rec["status"] == "failed"
        assert rec["error"] == "RPC timeout"

    def test_stats(self, gateway_kp):
        from src.ltp.gateway_vm.tracker import GatewayTracker

        tracker = GatewayTracker()
        a1 = _make_attestation(gateway_kp, "111")
        a2 = _make_attestation(gateway_kp, "222")
        a3 = _make_attestation(gateway_kp, "333")
        tracker.mark_pending(a1)
        tracker.mark_pending(a2)
        tracker.mark_pending(a3)
        tracker.mark_submitted(a1.event_id, tx_hash="0xt1")
        tracker.mark_failed(a3.event_id, error="err")
        stats = tracker.stats()
        assert stats["pending"] == 1
        assert stats["submitted"] == 1
        assert stats["failed"] == 1

    def test_get_by_status(self, gateway_kp):
        from src.ltp.gateway_vm.tracker import GatewayTracker

        tracker = GatewayTracker()
        a1 = _make_attestation(gateway_kp, "aaa")
        a2 = _make_attestation(gateway_kp, "bbb")
        tracker.mark_pending(a1)
        tracker.mark_pending(a2)
        tracker.mark_submitted(a1.event_id, tx_hash="0xt1")
        pending = tracker.get_by_status("pending")
        assert len(pending) == 1
        submitted = tracker.get_by_status("submitted")
        assert len(submitted) == 1

    def test_get_missing_returns_none(self):
        from src.ltp.gateway_vm.tracker import GatewayTracker

        tracker = GatewayTracker()
        assert tracker.get("nonexistent") is None

    def test_lookup_by_tx_hash(self, gateway_kp):
        from src.ltp.gateway_vm.tracker import GatewayTracker

        tracker = GatewayTracker()
        att = _make_attestation(gateway_kp)
        tracker.mark_pending(att)
        tracker.mark_submitted(att.event_id, tx_hash="0xdeadbeef")
        rec = tracker.lookup_by_tx_hash("0xdeadbeef")
        assert rec is not None
        assert rec["event_id"] == att.event_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_tracker.py -v`

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/tracker.py`:

```python
"""GatewayTracker — attestation lifecycle tracking for the gateway VM.

Wraps AnchorStatusTracker with gateway-specific convenience methods.
Tracks attestations from PENDING through SUBMITTED → CONFIRMED → FINALIZED,
with FAILED as a terminal error state.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from .writer import GatewayAttestation


class GatewayTracker:
    """Tracks gateway attestation lifecycle.

    Thread-safe. Returns snapshot dicts (not mutable refs).
    """

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_tracker.py -v`

Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/gateway_vm/tracker.py tests/test_gateway_vm_tracker.py
git commit -m "feat(gateway-vm): add GatewayTracker for attestation lifecycle tracking"
```

---

## Task 3: Gateway REST Endpoints — Status and Health

**Files:**
- Create: `src/ltp/gateway_vm/routers/__init__.py`
- Create: `src/ltp/gateway_vm/routers/status.py`
- Test: `tests/test_gateway_vm_routes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_vm_routes.py`:

```python
"""Tests for gateway REST endpoints."""

import pytest
from unittest.mock import MagicMock


def _make_test_app():
    """Create a FastAPI test app with gateway routers."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.ltp.gateway_vm.routers.status import router as status_router
    from src.ltp.gateway_vm.routers.events import router as events_router
    from src.ltp.gateway_vm.tracker import GatewayTracker

    app = FastAPI()
    app.include_router(status_router)
    app.include_router(events_router)

    # Wire state
    tracker = GatewayTracker()
    app.state.gateway_tracker = tracker
    app.state.gateway_service = MagicMock(
        running=True,
        epoch=42,
        retry_queue_size=0,
    )
    app.state.gateway_config = MagicMock(
        source_chain_id=84532,
        dest_chain_id=103115120,
        gateway_id="gw-test",
        challenge_mode="optimistic",
    )

    return TestClient(app), tracker


class TestGatewayStatus:
    def test_status_returns_ok(self):
        client, _ = _make_test_app()
        resp = client.get("/gateway/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"
        assert data["epoch"] == 42
        assert data["source_chain_id"] == 84532
        assert data["dest_chain_id"] == 103115120

    def test_status_degraded_when_retry_queue_large(self):
        client, _ = _make_test_app()
        client.app.state.gateway_service.retry_queue_size = 50
        resp = client.get("/gateway/status")
        data = resp.json()
        assert data["status"] == "degraded"

    def test_status_stopped_when_not_running(self):
        client, _ = _make_test_app()
        client.app.state.gateway_service.running = False
        resp = client.get("/gateway/status")
        data = resp.json()
        assert data["status"] == "stopped"


class TestGatewayHealth:
    def test_health_ok(self):
        client, _ = _make_test_app()
        resp = client.get("/gateway/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["checks"]["service"] == "running"

    def test_health_503_when_stopped(self):
        client, _ = _make_test_app()
        client.app.state.gateway_service.running = False
        resp = client.get("/gateway/health")
        assert resp.status_code == 503
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_routes.py::TestGatewayStatus tests/test_gateway_vm_routes.py::TestGatewayHealth -v`

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/routers/__init__.py`:

```python
"""Gateway VM REST routers."""
```

Create `src/ltp/gateway_vm/routers/status.py`:

```python
"""Gateway status and health endpoints.

GET /gateway/status — Current gateway state (active, degraded, stopped)
GET /gateway/health — Liveness + readiness (K8s probe compatible)
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/gateway", tags=["gateway"])

_RETRY_QUEUE_DEGRADED_THRESHOLD = 10


@router.get("/status")
async def gateway_status(request: Request) -> JSONResponse:
    """Current gateway operational status."""
    svc = request.app.state.gateway_service
    config = request.app.state.gateway_config
    tracker = request.app.state.gateway_tracker

    if not svc.running:
        status = "stopped"
    elif svc.retry_queue_size >= _RETRY_QUEUE_DEGRADED_THRESHOLD:
        status = "degraded"
    else:
        status = "active"

    return JSONResponse({
        "status": status,
        "gateway_id": config.gateway_id,
        "epoch": svc.epoch,
        "source_chain_id": config.source_chain_id,
        "dest_chain_id": config.dest_chain_id,
        "challenge_mode": config.challenge_mode,
        "retry_queue_size": svc.retry_queue_size,
        "tracker": tracker.stats(),
    })


@router.get("/health")
async def gateway_health(request: Request) -> JSONResponse:
    """K8s-compatible health probe."""
    svc = request.app.state.gateway_service

    checks = {
        "service": "running" if svc.running else "stopped",
        "retry_queue": "ok" if svc.retry_queue_size < _RETRY_QUEUE_DEGRADED_THRESHOLD else "degraded",
    }

    healthy = svc.running
    return JSONResponse(
        {"status": "ok" if healthy else "unhealthy", "checks": checks},
        status_code=200 if healthy else 503,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_routes.py::TestGatewayStatus tests/test_gateway_vm_routes.py::TestGatewayHealth -v`

Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/gateway_vm/routers/__init__.py src/ltp/gateway_vm/routers/status.py tests/test_gateway_vm_routes.py
git commit -m "feat(gateway-vm): add /gateway/status and /gateway/health REST endpoints"
```

---

## Task 4: Gateway REST Endpoints — Events

**Files:**
- Create: `src/ltp/gateway_vm/routers/events.py`
- Modify: `tests/test_gateway_vm_routes.py` (add event endpoint tests)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gateway_vm_routes.py`:

```python
class TestGatewayEvents:
    def _seed_tracker(self, tracker, gateway_kp):
        from src.ltp.gateway_vm.events import BridgeEvent
        from src.ltp.gateway_vm.writer import AttestationWriter, GatewayAttestation

        writer = AttestationWriter(operator_keypair=gateway_kp, dest_chain_id=103115120)
        events = []
        for i, suffix in enumerate(["aaa", "bbb", "ccc"]):
            event = BridgeEvent(
                source_chain_id=84532, bridge_contract="0x5083",
                tx_hash=f"0x{suffix}", block_number=100 + i, log_index=0,
                event_name="AnchorCreated", sender="0xaa", recipient="0xbb",
                payload_hash="sha3-256:ff", amount=0, nonce=i, timestamp=1700000000.0,
            )
            att = writer.create_attestation(event)
            tracker.mark_pending(att)
            events.append((event, att))
        # Submit first, fail third
        tracker.mark_submitted(events[0][1].event_id, tx_hash="0xaaa")
        tracker.mark_failed(events[2][1].event_id, error="timeout")
        return events

    def test_list_all_events(self):
        from src.ltp.keypair import KeyPair
        client, tracker = _make_test_app()
        kp = KeyPair.generate("events-test")
        self._seed_tracker(tracker, kp)
        resp = client.get("/gateway/events")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 3

    def test_filter_by_status(self):
        from src.ltp.keypair import KeyPair
        client, tracker = _make_test_app()
        kp = KeyPair.generate("filter-test")
        self._seed_tracker(tracker, kp)
        resp = client.get("/gateway/events?status=failed")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 1
        assert data["events"][0]["status"] == "failed"

    def test_lookup_by_tx_hash(self):
        from src.ltp.keypair import KeyPair
        client, tracker = _make_test_app()
        kp = KeyPair.generate("lookup-test")
        self._seed_tracker(tracker, kp)
        resp = client.get("/gateway/events/0xaaa")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tx_hash"] == "0xaaa"

    def test_lookup_missing_tx_hash_404(self):
        client, _ = _make_test_app()
        resp = client.get("/gateway/events/0xnonexistent")
        assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_routes.py::TestGatewayEvents -v`

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/routers/events.py`:

```python
"""Gateway event query endpoints.

GET /gateway/events?status=X — List processed events by status
GET /gateway/events/{tx_hash} — Single event lookup with full validation trace
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/gateway", tags=["gateway"])


def _serialize_record(rec: dict) -> dict:
    """Convert internal record to JSON-safe dict."""
    out = dict(rec)
    # Convert bytes fields to hex strings
    if isinstance(out.get("digest"), bytes):
        out["digest"] = out["digest"].hex()
    return out


@router.get("/events")
async def list_events(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status: pending|submitted|confirmed|finalized|failed"),
) -> JSONResponse:
    """List gateway-processed events, optionally filtered by status."""
    tracker = request.app.state.gateway_tracker

    if status:
        events = tracker.get_by_status(status)
    else:
        events = []
        for s in ("pending", "submitted", "confirmed", "finalized", "failed"):
            events.extend(tracker.get_by_status(s))

    return JSONResponse({
        "events": [_serialize_record(e) for e in events],
        "count": len(events),
    })


@router.get("/events/{tx_hash}")
async def lookup_event(request: Request, tx_hash: str) -> JSONResponse:
    """Look up a single event by its submission transaction hash."""
    tracker = request.app.state.gateway_tracker
    rec = tracker.lookup_by_tx_hash(tx_hash)
    if rec is None:
        return JSONResponse(
            {"error": f"no event found for tx_hash {tx_hash}"},
            status_code=404,
        )
    return JSONResponse(_serialize_record(rec))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_routes.py -v`

Expected: All tests PASS (5 status/health + 4 events = 9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/gateway_vm/routers/events.py tests/test_gateway_vm_routes.py
git commit -m "feat(gateway-vm): add /gateway/events and /gateway/events/{tx_hash} endpoints"
```

---

## Task 5: End-to-End Integration Test

**Files:**
- Create: `tests/test_gateway_vm_e2e.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_gateway_vm_e2e.py`:

```python
"""End-to-end integration test: event → validate → attest → anchor → track.

This test exercises the full gateway VM pipeline with mock RPC but real
cryptography, real SQLite replay DB, and real attestation signing.
"""

import pytest
from unittest.mock import MagicMock
from src.ltp.keypair import KeyPair


@pytest.fixture(scope="module")
def gateway_kp():
    return KeyPair.generate("e2e-gateway")


def _make_raw_log(tx_hash="0xabc", block_number=100, log_index=0):
    return {
        "transactionHash": tx_hash,
        "blockNumber": block_number,
        "logIndex": log_index,
        "address": "0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
        "event": "AnchorCreated",
        "args": {
            "sender": "0xdeadbeef",
            "recipient": "0xcafebabe",
            "payloadHash": "sha3-256:abcd1234",
            "amount": 100_000_000,
            "nonce": 1,
        },
    }


class TestEndToEndSingleEvent:
    """One event flows through the entire pipeline."""

    def test_single_event_end_to_end(self, gateway_kp):
        from src.ltp.gateway_vm.config import GatewayVMConfig
        from src.ltp.gateway_vm.service import GatewayVMService
        from src.ltp.gateway_vm.tracker import GatewayTracker

        config = GatewayVMConfig(
            enabled=True,
            source_chain_id=84532,
            source_bridge_contract="0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
            finality_depth=12,
            dest_chain_id=103115120,
            replay_db_path=":memory:",
        )

        tracker = GatewayTracker()
        anchored_attestations = []

        def mock_anchor(attestation):
            """Simulate successful anchor and track lifecycle."""
            tracker.mark_pending(attestation)
            tracker.mark_submitted(attestation.event_id, tx_hash="0xdevnet_tx_001")
            anchored_attestations.append(attestation)
            return "0xdevnet_tx_001"

        svc = GatewayVMService(
            config=config,
            operator_keypair=gateway_kp,
            fetch_logs=lambda fb, tb: [_make_raw_log("0xsource_tx_001", 100, 0)],
            get_source_block_number=lambda: 200,
            get_dest_block_number=lambda: 999,
            anchor_fn=mock_anchor,
            is_signer_authorized=lambda: True,
        )

        # Execute one tick
        result = svc.tick()

        # Verify pipeline results
        assert result.events_observed == 1
        assert result.events_accepted == 1
        assert result.events_rejected == 0
        assert result.anchor_failures == 0

        # Verify attestation was created with valid signature
        assert len(anchored_attestations) == 1
        att = anchored_attestations[0]
        assert att.source_chain_id == 84532
        assert att.dest_chain_id == 103115120
        assert att.verify(gateway_kp.vk) is True

        # Verify tracker recorded the lifecycle
        stats = tracker.stats()
        assert stats["submitted"] == 1

        # Verify replay protection prevents re-processing
        result2 = svc.tick()
        assert result2.events_observed == 1
        assert result2.events_rejected == 1  # replay rejection
        assert result2.events_accepted == 0


class TestEndToEndMultiEvent:
    """Multiple events in rapid succession."""

    def test_three_events_batch(self, gateway_kp):
        from src.ltp.gateway_vm.config import GatewayVMConfig
        from src.ltp.gateway_vm.service import GatewayVMService

        config = GatewayVMConfig(
            enabled=True,
            source_chain_id=84532,
            source_bridge_contract="0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
            finality_depth=12,
            dest_chain_id=103115120,
            replay_db_path=":memory:",
        )

        logs = [
            _make_raw_log("0xevent_001", 100, 0),
            _make_raw_log("0xevent_002", 101, 0),
            _make_raw_log("0xevent_003", 102, 0),
        ]
        anchor_fn = MagicMock(return_value="0xdevnet_tx")

        svc = GatewayVMService(
            config=config,
            operator_keypair=gateway_kp,
            fetch_logs=lambda fb, tb: logs,
            get_source_block_number=lambda: 200,
            get_dest_block_number=lambda: 999,
            anchor_fn=anchor_fn,
            is_signer_authorized=lambda: True,
        )

        result = svc.tick()
        assert result.events_observed == 3
        assert result.events_accepted == 3
        assert anchor_fn.call_count == 3

        # All three signatures verify
        for call_args in anchor_fn.call_args_list:
            att = call_args[0][0]
            assert att.verify(gateway_kp.vk)


class TestEndToEndAnchorFailureAndRetry:
    """Anchor failure → retry queue → successful retry."""

    def test_failure_and_retry(self, gateway_kp):
        from src.ltp.gateway_vm.config import GatewayVMConfig
        from src.ltp.gateway_vm.service import GatewayVMService

        config = GatewayVMConfig(
            enabled=True,
            source_chain_id=84532,
            source_bridge_contract="0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
            finality_depth=12,
            dest_chain_id=103115120,
            replay_db_path=":memory:",
            max_retries=3,
        )

        call_count = {"n": 0}

        def flaky_anchor(attestation):
            call_count["n"] += 1
            if call_count["n"] <= 1:
                raise RuntimeError("RPC timeout")
            return "0xdevnet_tx"

        svc = GatewayVMService(
            config=config,
            operator_keypair=gateway_kp,
            fetch_logs=lambda fb, tb: [_make_raw_log("0xflaky", 100, 0)],
            get_source_block_number=lambda: 200,
            get_dest_block_number=lambda: 999,
            anchor_fn=flaky_anchor,
            is_signer_authorized=lambda: True,
        )

        # Tick 1: event observed, anchor fails, enters retry queue
        r1 = svc.tick()
        assert r1.events_observed == 1
        assert r1.anchor_failures == 1
        assert svc.retry_queue_size == 1

        # Tick 2: retry succeeds
        r2 = svc.tick()
        assert r2.retries_attempted == 1
        assert svc.retry_queue_size == 0


class TestEndToEndValidationRejections:
    """Events rejected for various validation failures."""

    def test_wrong_chain_id_rejected(self, gateway_kp):
        from src.ltp.gateway_vm.config import GatewayVMConfig
        from src.ltp.gateway_vm.service import GatewayVMService

        config = GatewayVMConfig(
            enabled=True,
            source_chain_id=84532,
            source_bridge_contract="0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
            finality_depth=12,
            dest_chain_id=103115120,
            replay_db_path=":memory:",
        )

        # Log has wrong contract address
        bad_log = _make_raw_log("0xbad", 100, 0)
        bad_log["address"] = "0xWRONGCONTRACT"

        svc = GatewayVMService(
            config=config,
            operator_keypair=gateway_kp,
            fetch_logs=lambda fb, tb: [bad_log],
            get_source_block_number=lambda: 200,
            get_dest_block_number=lambda: 999,
            anchor_fn=MagicMock(return_value="0x"),
            is_signer_authorized=lambda: True,
        )

        result = svc.tick()
        assert result.events_observed == 1
        assert result.events_rejected == 1
        assert result.events_accepted == 0
```

- [ ] **Step 2: Run the integration tests**

Run: `pytest tests/test_gateway_vm_e2e.py -v`

Expected: All 4 test classes PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_gateway_vm_e2e.py
git commit -m "test(gateway-vm): add end-to-end integration tests for full attestation pipeline"
```

---

## Task 6: Bidirectional Flow Tests

**Files:**
- Create: `tests/test_gateway_vm_bidirectional.py`

- [ ] **Step 1: Write the bidirectional tests**

Create `tests/test_gateway_vm_bidirectional.py`:

```python
"""Bidirectional integration tests: Base Sepolia → GSX and GSX → Base Sepolia.

Tests both directions of the gateway pipeline with independent configs,
replay DBs, and attestation writers.
"""

import pytest
from unittest.mock import MagicMock
from src.ltp.keypair import KeyPair


@pytest.fixture(scope="module")
def base_to_gsx_kp():
    return KeyPair.generate("base-to-gsx-gateway")


@pytest.fixture(scope="module")
def gsx_to_base_kp():
    return KeyPair.generate("gsx-to-base-gateway")


def _make_raw_log(tx_hash, block_number, contract_address):
    return {
        "transactionHash": tx_hash,
        "blockNumber": block_number,
        "logIndex": 0,
        "address": contract_address,
        "event": "AnchorCreated",
        "args": {
            "sender": "0xsender",
            "recipient": "0xrecipient",
            "payloadHash": "sha3-256:data",
            "amount": 1_000_000,
            "nonce": 0,
        },
    }


class TestBaseSepoliaToGSX:
    """Base Sepolia (84532) → GSX Testnet (103115120)."""

    def test_base_event_anchored_to_gsx(self, base_to_gsx_kp):
        from src.ltp.gateway_vm.config import GatewayVMConfig
        from src.ltp.gateway_vm.service import GatewayVMService

        config = GatewayVMConfig(
            enabled=True,
            source_chain_id=84532,
            source_bridge_contract="0x79eF1B7914f98C5C1404617449AB1f377c475996",
            finality_depth=12,
            dest_chain_id=103115120,
            replay_db_path=":memory:",
        )

        base_log = _make_raw_log(
            "0xbase_tx_001", 100,
            "0x79eF1B7914f98C5C1404617449AB1f377c475996",
        )
        anchor_fn = MagicMock(return_value="0xgsx_anchor_tx")

        svc = GatewayVMService(
            config=config,
            operator_keypair=base_to_gsx_kp,
            fetch_logs=lambda fb, tb: [base_log],
            get_source_block_number=lambda: 200,
            get_dest_block_number=lambda: 999,
            anchor_fn=anchor_fn,
            is_signer_authorized=lambda: True,
        )

        result = svc.tick()
        assert result.events_accepted == 1

        att = anchor_fn.call_args[0][0]
        assert att.source_chain_id == 84532
        assert att.dest_chain_id == 103115120
        assert att.verify(base_to_gsx_kp.vk)


class TestGSXToBaseSepolia:
    """GSX Testnet (103115120) → Base Sepolia (84532)."""

    def test_gsx_event_anchored_to_base(self, gsx_to_base_kp):
        from src.ltp.gateway_vm.config import GatewayVMConfig
        from src.ltp.gateway_vm.service import GatewayVMService

        config = GatewayVMConfig(
            enabled=True,
            source_chain_id=103115120,
            source_bridge_contract="0xB29d8BFF4973D1D7bcB10E32112EBB8fdd530bF4",
            finality_depth=6,  # fewer confirmations on GSX devnet
            dest_chain_id=84532,
            replay_db_path=":memory:",
        )

        gsx_log = _make_raw_log(
            "0xgsx_tx_001", 500,
            "0xB29d8BFF4973D1D7bcB10E32112EBB8fdd530bF4",
        )
        anchor_fn = MagicMock(return_value="0xbase_anchor_tx")

        svc = GatewayVMService(
            config=config,
            operator_keypair=gsx_to_base_kp,
            fetch_logs=lambda fb, tb: [gsx_log],
            get_source_block_number=lambda: 600,
            get_dest_block_number=lambda: 999,
            anchor_fn=anchor_fn,
            is_signer_authorized=lambda: True,
        )

        result = svc.tick()
        assert result.events_accepted == 1

        att = anchor_fn.call_args[0][0]
        assert att.source_chain_id == 103115120
        assert att.dest_chain_id == 84532
        assert att.verify(gsx_to_base_kp.vk)


class TestBidirectionalIsolation:
    """Both directions running simultaneously don't interfere."""

    def test_independent_replay_dbs(self, base_to_gsx_kp, gsx_to_base_kp):
        from src.ltp.gateway_vm.config import GatewayVMConfig
        from src.ltp.gateway_vm.service import GatewayVMService

        base_config = GatewayVMConfig(
            source_chain_id=84532,
            source_bridge_contract="0x79eF1B7914f98C5C1404617449AB1f377c475996",
            finality_depth=12, dest_chain_id=103115120,
            replay_db_path=":memory:",
        )
        gsx_config = GatewayVMConfig(
            source_chain_id=103115120,
            source_bridge_contract="0xB29d8BFF4973D1D7bcB10E32112EBB8fdd530bF4",
            finality_depth=6, dest_chain_id=84532,
            replay_db_path=":memory:",
        )

        base_log = _make_raw_log("0xshared_hash", 100,
                                  "0x79eF1B7914f98C5C1404617449AB1f377c475996")
        gsx_log = _make_raw_log("0xshared_hash", 100,
                                 "0xB29d8BFF4973D1D7bcB10E32112EBB8fdd530bF4")

        base_anchor = MagicMock(return_value="0xb")
        gsx_anchor = MagicMock(return_value="0xg")

        svc_base = GatewayVMService(
            config=base_config, operator_keypair=base_to_gsx_kp,
            fetch_logs=lambda fb, tb: [base_log],
            get_source_block_number=lambda: 200,
            get_dest_block_number=lambda: 999,
            anchor_fn=base_anchor, is_signer_authorized=lambda: True,
        )
        svc_gsx = GatewayVMService(
            config=gsx_config, operator_keypair=gsx_to_base_kp,
            fetch_logs=lambda fb, tb: [gsx_log],
            get_source_block_number=lambda: 200,
            get_dest_block_number=lambda: 999,
            anchor_fn=gsx_anchor, is_signer_authorized=lambda: True,
        )

        r_base = svc_base.tick()
        r_gsx = svc_gsx.tick()

        # Both accept independently — same tx_hash on different chains is not a replay
        assert r_base.events_accepted == 1
        assert r_gsx.events_accepted == 1
        assert base_anchor.call_count == 1
        assert gsx_anchor.call_count == 1
```

- [ ] **Step 2: Run the bidirectional tests**

Run: `pytest tests/test_gateway_vm_bidirectional.py -v`

Expected: All 3 test classes PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_gateway_vm_bidirectional.py
git commit -m "test(gateway-vm): add bidirectional flow tests for Base Sepolia ↔ GSX"
```

---

## Task 7: Grafana Dashboard Template

**Files:**
- Modify: `deploy/observability/grafana/dashboards/` (add gateway dashboard)
- Modify: `deploy/observability/prometheus/alerts.yml` (add gateway alerts)

- [ ] **Step 1: Verify existing dashboard location**

Run: `ls deploy/observability/grafana/dashboards/`

Expected: `etp-node.json` exists

- [ ] **Step 2: Create gateway dashboard JSON**

Create `deploy/observability/grafana/dashboards/etp-gateway.json`:

```json
{
  "dashboard": {
    "uid": "etp-gateway-vm",
    "title": "ETP Gateway VM",
    "tags": ["etp", "gateway"],
    "timezone": "browser",
    "refresh": "15s",
    "templating": {
      "list": [
        {
          "name": "gateway_id",
          "type": "query",
          "query": "label_values(etp_gateway_events_observed, gateway_id)",
          "datasource": { "uid": "prometheus" }
        }
      ]
    },
    "panels": [
      {
        "title": "Events Observed",
        "type": "stat",
        "gridPos": { "h": 4, "w": 6, "x": 0, "y": 0 },
        "targets": [{ "expr": "etp_gateway_events_observed{gateway_id=\"$gateway_id\"}" }]
      },
      {
        "title": "Events Accepted",
        "type": "stat",
        "gridPos": { "h": 4, "w": 6, "x": 6, "y": 0 },
        "targets": [{ "expr": "etp_gateway_events_accepted{gateway_id=\"$gateway_id\"}" }]
      },
      {
        "title": "Events Rejected",
        "type": "stat",
        "gridPos": { "h": 4, "w": 6, "x": 12, "y": 0 },
        "targets": [{ "expr": "etp_gateway_events_rejected{gateway_id=\"$gateway_id\"}" }]
      },
      {
        "title": "Replay Rejections",
        "type": "stat",
        "gridPos": { "h": 4, "w": 6, "x": 18, "y": 0 },
        "targets": [{ "expr": "etp_gateway_replay_rejections{gateway_id=\"$gateway_id\"}" }]
      },
      {
        "title": "Anchor Latency",
        "type": "timeseries",
        "gridPos": { "h": 8, "w": 12, "x": 0, "y": 4 },
        "targets": [{ "expr": "histogram_quantile(0.95, rate(etp_gateway_anchor_latency_bucket{gateway_id=\"$gateway_id\"}[5m]))" }],
        "fieldConfig": { "defaults": { "unit": "s" } }
      },
      {
        "title": "Finality Wait",
        "type": "timeseries",
        "gridPos": { "h": 8, "w": 12, "x": 12, "y": 4 },
        "targets": [{ "expr": "histogram_quantile(0.95, rate(etp_gateway_finality_wait_bucket{gateway_id=\"$gateway_id\"}[5m]))" }],
        "fieldConfig": { "defaults": { "unit": "s" } }
      },
      {
        "title": "Rejection Reasons",
        "type": "piechart",
        "gridPos": { "h": 8, "w": 12, "x": 0, "y": 12 },
        "targets": [{ "expr": "etp_gateway_events_rejected{gateway_id=\"$gateway_id\"}", "legendFormat": "{{reason}}" }]
      },
      {
        "title": "Events Rate (per minute)",
        "type": "timeseries",
        "gridPos": { "h": 8, "w": 12, "x": 12, "y": 12 },
        "targets": [
          { "expr": "rate(etp_gateway_events_observed{gateway_id=\"$gateway_id\"}[1m]) * 60", "legendFormat": "observed" },
          { "expr": "rate(etp_gateway_events_accepted{gateway_id=\"$gateway_id\"}[1m]) * 60", "legendFormat": "accepted" },
          { "expr": "rate(etp_gateway_events_rejected{gateway_id=\"$gateway_id\"}[1m]) * 60", "legendFormat": "rejected" }
        ]
      }
    ]
  }
}
```

- [ ] **Step 3: Add gateway alert rules**

Add to `deploy/observability/prometheus/alerts.yml` under a new group:

```yaml
  - name: etp-gateway
    rules:
      - alert: ETPGatewayDown
        expr: up{job="etp-gateway"} == 0
        for: 2m
        labels:
          severity: critical
          component: gateway
        annotations:
          summary: "Gateway VM {{ $labels.gateway_id }} is down"

      - alert: ETPGatewayHighRejectionRate
        expr: rate(etp_gateway_events_rejected[5m]) / rate(etp_gateway_events_observed[5m]) > 0.5
        for: 5m
        labels:
          severity: warning
          component: gateway
        annotations:
          summary: "Gateway {{ $labels.gateway_id }} rejecting >50% of events"

      - alert: ETPGatewayAnchorLatencyHigh
        expr: histogram_quantile(0.95, rate(etp_gateway_anchor_latency_bucket[5m])) > 30
        for: 5m
        labels:
          severity: warning
          component: gateway
        annotations:
          summary: "Gateway {{ $labels.gateway_id }} p95 anchor latency >30s"
```

- [ ] **Step 4: Commit**

```bash
git add deploy/observability/grafana/dashboards/etp-gateway.json deploy/observability/prometheus/alerts.yml
git commit -m "ops(gateway-vm): add Grafana dashboard and Prometheus alert rules"
```

---

## Task 8: Full Regression

**Files:**
- All `src/ltp/gateway_vm/` files
- All `tests/test_gateway_vm_*.py` files

- [ ] **Step 1: Run the entire test suite**

Run: `pytest tests/ -v --tb=short 2>&1 | tail -40`

Expected: All existing tests PASS. No regressions. All gateway VM tests PASS.

- [ ] **Step 2: Verify all gateway imports**

Run: `python -c "from src.ltp.gateway_vm import GatewayVMService, GatewayVM, DevnetAnchorClient, GatewayTracker, FinalityWatcher; print('Phase 2 imports OK')"`

Expected: `Phase 2 imports OK`

- [ ] **Step 3: Count new test files and tests**

Run: `pytest tests/test_gateway_vm_*.py --co -q 2>&1 | tail -5`

Expected: ~80+ tests collected across 11+ test files

- [ ] **Step 4: Final commit (if any uncommitted changes)**

```bash
git status
# If clean: nothing to do
# If changes: commit with appropriate message
```

---

## Summary

| Task | Component | Files | Tests |
|---|---|---|---|
| 1 | DevnetAnchorClient | `anchor_client.py` | 5 tests |
| 2 | GatewayTracker | `tracker.py` | 7 tests |
| 3 | REST: Status + Health | `routers/status.py` | 5 tests |
| 4 | REST: Events | `routers/events.py` | 4 tests |
| 5 | E2E Integration | `test_gateway_vm_e2e.py` | 4 tests |
| 6 | Bidirectional | `test_gateway_vm_bidirectional.py` | 3 tests |
| 7 | Grafana + Alerts | dashboard JSON, alerts YAML | 0 (ops artifact) |
| 8 | Regression | All | Full suite |

**Total: ~5 new files, ~700 lines of code, ~28 tests, 8 commits.**

**What this plan does NOT cover (separate plans):**
- Phase 3: 15 stress test scenarios under adversarial conditions
- Phase 4: MoveVM integration, BLS attestation, writer permissioning
- Multi-source chain support (multiple source chains simultaneously) — deferred to Phase 3 or operational readiness
