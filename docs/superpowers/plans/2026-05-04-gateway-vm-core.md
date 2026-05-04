# Gateway VM Core — Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `src/ltp/gateway_vm/` module — a hardened single-VM POA attestation gateway that watches external chain bridge events, validates them against a 12-point checklist, signs LTP commitment records, and anchors them to the GSX devnet.

**Architecture:** Daemon thread (following `BridgeOperatorService` pattern) with a deterministic `tick()` method for testable, event-driven processing. Each tick: poll for new events, validate, write commitment, sign with ML-DSA-65, anchor to devnet. SQLite-backed replay protection prevents duplicate processing. All components are independently testable via constructor injection.

**Tech Stack:** Python 3.12+, ML-DSA-65 (pqcrypto), SHA3-256, SQLite3 (stdlib), threading (stdlib), pytest, existing LTP primitives (`domain.py`, `sequencing.py`, `commitment.py`, `observability/`)

**Spec:** `docs/LTP_GATEWAY_VM_PLAN.md` — Phase 1 deliverables (Sections 4-5)

---

## File Structure

| File | Responsibility |
|---|---|
| `src/ltp/gateway_vm/__init__.py` | Public API exports |
| `src/ltp/gateway_vm/config.py` | `GatewayVMConfig` dataclass — all gateway settings |
| `src/ltp/gateway_vm/events.py` | `BridgeEvent` dataclass — normalized external chain event |
| `src/ltp/gateway_vm/listener.py` | `EventListener` — polls external chain for bridge events (injectable RPC) |
| `src/ltp/gateway_vm/validator.py` | `EventValidator` — 12-point validation checklist |
| `src/ltp/gateway_vm/replay.py` | `ReplayDB` — SQLite-backed per-source-chain event deduplication |
| `src/ltp/gateway_vm/writer.py` | `AttestationWriter` — creates LTP commitment + ML-DSA-65 signature |
| `src/ltp/gateway_vm/service.py` | `GatewayVMService` — daemon tick loop wiring all components |
| `src/ltp/gateway_vm/metrics.py` | `create_gateway_metrics()` — registers gateway-specific counters/histograms |
| `src/ltp/domain.py` | **Modify:** add `DOMAIN_GATEWAY_ATTEST` and `DOMAIN_EXTERNAL_EVENT` tags |
| `tests/test_gateway_vm_config.py` | Config parsing and defaults |
| `tests/test_gateway_vm_events.py` | BridgeEvent construction and serialization |
| `tests/test_gateway_vm_replay.py` | SQLite replay DB operations |
| `tests/test_gateway_vm_validator.py` | 12-point validation logic |
| `tests/test_gateway_vm_writer.py` | Commitment creation and signing |
| `tests/test_gateway_vm_listener.py` | Event listener with mock RPC |
| `tests/test_gateway_vm_service.py` | Full service tick integration |
| `tests/test_gateway_vm_metrics.py` | Metrics registration and increment |

---

## Task 1: Domain Separation Tags

**Files:**
- Modify: `src/ltp/domain.py:74-107`
- Test: `tests/test_domain_separation.py` (existing, add assertions)

- [ ] **Step 1: Write the failing test**

Open `tests/test_domain_separation.py` and add:

```python
def test_gateway_attest_domain_tag_exists():
    from src.ltp.domain import DOMAIN_GATEWAY_ATTEST
    assert DOMAIN_GATEWAY_ATTEST == b"GSX-LTP:gateway-attest:v1\x00"


def test_external_event_domain_tag_exists():
    from src.ltp.domain import DOMAIN_EXTERNAL_EVENT
    assert DOMAIN_EXTERNAL_EVENT == b"GSX-LTP:external-event:v1\x00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_domain_separation.py::test_gateway_attest_domain_tag_exists tests/test_domain_separation.py::test_external_event_domain_tag_exists -v`

Expected: FAIL with `ImportError: cannot import name 'DOMAIN_GATEWAY_ATTEST'`

- [ ] **Step 3: Add domain tags to domain.py**

In `src/ltp/domain.py`, after line 74 (`DOMAIN_ZK_TRANSFER`), add:

```python
DOMAIN_GATEWAY_ATTEST   = b"GSX-LTP:gateway-attest:v1\x00"
DOMAIN_EXTERNAL_EVENT   = b"GSX-LTP:external-event:v1\x00"
```

Add both to `__all__`:

```python
    "DOMAIN_GATEWAY_ATTEST",
    "DOMAIN_EXTERNAL_EVENT",
```

Add both to `_ALL_TAGS`:

```python
    "DOMAIN_GATEWAY_ATTEST": DOMAIN_GATEWAY_ATTEST,
    "DOMAIN_EXTERNAL_EVENT": DOMAIN_EXTERNAL_EVENT,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_domain_separation.py::test_gateway_attest_domain_tag_exists tests/test_domain_separation.py::test_external_event_domain_tag_exists -v`

Expected: PASS

- [ ] **Step 5: Run full domain separation test suite for regression**

Run: `pytest tests/test_domain_separation.py -v`

Expected: All tests PASS (collision check at import time verifies no duplicate tags)

- [ ] **Step 6: Commit**

```bash
git add src/ltp/domain.py tests/test_domain_separation.py
git commit -m "feat(gateway-vm): add DOMAIN_GATEWAY_ATTEST and DOMAIN_EXTERNAL_EVENT tags"
```

---

## Task 2: Gateway VM Config

**Files:**
- Create: `src/ltp/gateway_vm/__init__.py`
- Create: `src/ltp/gateway_vm/config.py`
- Test: `tests/test_gateway_vm_config.py`

- [ ] **Step 1: Create the package directory**

```bash
mkdir -p src/ltp/gateway_vm
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_gateway_vm_config.py`:

```python
"""Tests for GatewayVMConfig."""

import os
import pytest


class TestGatewayVMConfigDefaults:
    def test_defaults(self):
        from src.ltp.gateway_vm.config import GatewayVMConfig

        cfg = GatewayVMConfig()
        assert cfg.enabled is False
        assert cfg.mode == "poa-attestation"
        assert cfg.source_chain_id == 84532
        assert cfg.source_rpc_url == ""
        assert cfg.source_bridge_contract == ""
        assert cfg.finality_depth == 12
        assert cfg.poll_interval_seconds == 5.0
        assert cfg.dest_chain_id == 103115120
        assert cfg.dest_rpc_url == ""
        assert cfg.dest_registry_address == ""
        assert cfg.replay_db_path == ":memory:"
        assert cfg.max_retries == 5
        assert cfg.retry_interval_seconds == 30.0
        assert cfg.challenge_mode == "optimistic"
        assert cfg.challenge_period_seconds == 3600.0
        assert cfg.metrics_port == 9090
        assert cfg.log_level == "info"
        assert cfg.gateway_id == "gateway-vm-0"


class TestGatewayVMConfigFromEnv:
    def test_from_env_overrides(self):
        from src.ltp.gateway_vm.config import GatewayVMConfig

        env = {
            "ETP_GATEWAY_VM_ENABLED": "true",
            "ETP_GATEWAY_VM_SOURCE_CHAIN_ID": "1",
            "ETP_GATEWAY_VM_SOURCE_RPC_URL": "https://rpc.example.com",
            "ETP_GATEWAY_VM_SOURCE_BRIDGE_CONTRACT": "0xabc123",
            "ETP_GATEWAY_VM_FINALITY_DEPTH": "20",
            "ETP_GATEWAY_VM_POLL_INTERVAL": "10",
            "ETP_GATEWAY_VM_DEST_CHAIN_ID": "42",
            "ETP_GATEWAY_VM_DEST_RPC_URL": "https://devnet.example.com",
            "ETP_GATEWAY_VM_DEST_REGISTRY": "0xdef456",
            "ETP_GATEWAY_VM_REPLAY_DB_PATH": "/tmp/replay.db",
            "ETP_GATEWAY_VM_MAX_RETRIES": "3",
            "ETP_GATEWAY_VM_CHALLENGE_MODE": "zk",
            "ETP_GATEWAY_VM_GATEWAY_ID": "gw-1",
        }
        for k, v in env.items():
            os.environ[k] = v
        try:
            cfg = GatewayVMConfig.from_env()
            assert cfg.enabled is True
            assert cfg.source_chain_id == 1
            assert cfg.source_rpc_url == "https://rpc.example.com"
            assert cfg.source_bridge_contract == "0xabc123"
            assert cfg.finality_depth == 20
            assert cfg.poll_interval_seconds == 10.0
            assert cfg.dest_chain_id == 42
            assert cfg.dest_rpc_url == "https://devnet.example.com"
            assert cfg.dest_registry_address == "0xdef456"
            assert cfg.replay_db_path == "/tmp/replay.db"
            assert cfg.max_retries == 3
            assert cfg.challenge_mode == "zk"
            assert cfg.gateway_id == "gw-1"
        finally:
            for k in env:
                os.environ.pop(k, None)

    def test_from_env_defaults_when_unset(self):
        from src.ltp.gateway_vm.config import GatewayVMConfig

        # Clear any gateway env vars
        for k in list(os.environ):
            if k.startswith("ETP_GATEWAY_VM_"):
                del os.environ[k]

        cfg = GatewayVMConfig.from_env()
        assert cfg.enabled is False
        assert cfg.source_chain_id == 84532
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_config.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.ltp.gateway_vm'`

- [ ] **Step 4: Write the implementation**

Create `src/ltp/gateway_vm/__init__.py`:

```python
"""Gateway VM — POA attestation gateway for GSX devnet."""

from .config import GatewayVMConfig

__all__ = ["GatewayVMConfig"]
```

Create `src/ltp/gateway_vm/config.py`:

```python
"""Gateway VM configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class GatewayVMConfig:
    """Configuration for the Gateway VM service.

    All fields have safe defaults. Use from_env() to overlay
    with ETP_GATEWAY_VM_* environment variables.
    """

    # General
    enabled: bool = False
    mode: str = "poa-attestation"
    gateway_id: str = "gateway-vm-0"

    # Source chain (external testnet to watch)
    source_chain_id: int = 84532  # Base Sepolia
    source_rpc_url: str = ""
    source_bridge_contract: str = ""
    finality_depth: int = 12
    poll_interval_seconds: float = 5.0

    # Destination chain (GSX devnet to anchor into)
    dest_chain_id: int = 103115120  # GSX Testnet
    dest_rpc_url: str = ""
    dest_registry_address: str = ""

    # Validation
    replay_db_path: str = ":memory:"
    max_retries: int = 5
    retry_interval_seconds: float = 30.0
    challenge_mode: str = "optimistic"  # "optimistic" | "zk" | "disabled"
    challenge_period_seconds: float = 3600.0

    # Observability
    metrics_port: int = 9090
    log_level: str = "info"

    @classmethod
    def from_env(cls) -> GatewayVMConfig:
        """Create config from ETP_GATEWAY_VM_* environment variables."""

        def _bool(key: str, default: bool) -> bool:
            val = os.environ.get(key, "")
            if not val:
                return default
            return val.lower() in ("true", "1", "yes")

        def _int(key: str, default: int) -> int:
            val = os.environ.get(key, "")
            return int(val) if val else default

        def _float(key: str, default: float) -> float:
            val = os.environ.get(key, "")
            return float(val) if val else default

        def _str(key: str, default: str) -> str:
            return os.environ.get(key, default)

        return cls(
            enabled=_bool("ETP_GATEWAY_VM_ENABLED", False),
            mode=_str("ETP_GATEWAY_VM_MODE", "poa-attestation"),
            gateway_id=_str("ETP_GATEWAY_VM_GATEWAY_ID", "gateway-vm-0"),
            source_chain_id=_int("ETP_GATEWAY_VM_SOURCE_CHAIN_ID", 84532),
            source_rpc_url=_str("ETP_GATEWAY_VM_SOURCE_RPC_URL", ""),
            source_bridge_contract=_str("ETP_GATEWAY_VM_SOURCE_BRIDGE_CONTRACT", ""),
            finality_depth=_int("ETP_GATEWAY_VM_FINALITY_DEPTH", 12),
            poll_interval_seconds=_float("ETP_GATEWAY_VM_POLL_INTERVAL", 5.0),
            dest_chain_id=_int("ETP_GATEWAY_VM_DEST_CHAIN_ID", 103115120),
            dest_rpc_url=_str("ETP_GATEWAY_VM_DEST_RPC_URL", ""),
            dest_registry_address=_str("ETP_GATEWAY_VM_DEST_REGISTRY", ""),
            replay_db_path=_str("ETP_GATEWAY_VM_REPLAY_DB_PATH", ":memory:"),
            max_retries=_int("ETP_GATEWAY_VM_MAX_RETRIES", 5),
            retry_interval_seconds=_float("ETP_GATEWAY_VM_RETRY_INTERVAL", 30.0),
            challenge_mode=_str("ETP_GATEWAY_VM_CHALLENGE_MODE", "optimistic"),
            challenge_period_seconds=_float("ETP_GATEWAY_VM_CHALLENGE_PERIOD", 3600.0),
            metrics_port=_int("ETP_GATEWAY_VM_METRICS_PORT", 9090),
            log_level=_str("ETP_GATEWAY_VM_LOG_LEVEL", "info"),
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_config.py -v`

Expected: PASS (all 3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/ltp/gateway_vm/__init__.py src/ltp/gateway_vm/config.py tests/test_gateway_vm_config.py
git commit -m "feat(gateway-vm): add GatewayVMConfig with env var overlay"
```

---

## Task 3: BridgeEvent Data Type

**Files:**
- Create: `src/ltp/gateway_vm/events.py`
- Test: `tests/test_gateway_vm_events.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_vm_events.py`:

```python
"""Tests for BridgeEvent — normalized external chain event."""

import time
import pytest


class TestBridgeEventConstruction:
    def test_create_bridge_event(self):
        from src.ltp.gateway_vm.events import BridgeEvent

        event = BridgeEvent(
            source_chain_id=84532,
            bridge_contract="0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
            tx_hash="0xabc123",
            block_number=100,
            log_index=0,
            event_name="AnchorCreated",
            sender="0xdeadbeef",
            recipient="0xcafebabe",
            payload_hash="sha3-256:abcd1234",
            amount=100_000_000,
            nonce=1,
            timestamp=1700000000.0,
        )
        assert event.source_chain_id == 84532
        assert event.tx_hash == "0xabc123"
        assert event.block_number == 100
        assert event.nonce == 1

    def test_event_id_is_deterministic(self):
        from src.ltp.gateway_vm.events import BridgeEvent

        kwargs = dict(
            source_chain_id=84532,
            bridge_contract="0xabc",
            tx_hash="0x123",
            block_number=50,
            log_index=0,
            event_name="AnchorCreated",
            sender="0xaa",
            recipient="0xbb",
            payload_hash="sha3-256:ff",
            amount=0,
            nonce=0,
            timestamp=1700000000.0,
        )
        e1 = BridgeEvent(**kwargs)
        e2 = BridgeEvent(**kwargs)
        assert e1.event_id == e2.event_id
        assert len(e1.event_id) > 0

    def test_different_events_different_ids(self):
        from src.ltp.gateway_vm.events import BridgeEvent

        base = dict(
            source_chain_id=84532,
            bridge_contract="0xabc",
            tx_hash="0x123",
            block_number=50,
            log_index=0,
            event_name="AnchorCreated",
            sender="0xaa",
            recipient="0xbb",
            payload_hash="sha3-256:ff",
            amount=0,
            nonce=0,
            timestamp=1700000000.0,
        )
        e1 = BridgeEvent(**base)
        e2 = BridgeEvent(**{**base, "tx_hash": "0x456"})
        assert e1.event_id != e2.event_id

    def test_to_signable_bytes(self):
        from src.ltp.gateway_vm.events import BridgeEvent

        event = BridgeEvent(
            source_chain_id=84532,
            bridge_contract="0xabc",
            tx_hash="0x123",
            block_number=50,
            log_index=0,
            event_name="AnchorCreated",
            sender="0xaa",
            recipient="0xbb",
            payload_hash="sha3-256:ff",
            amount=0,
            nonce=0,
            timestamp=1700000000.0,
        )
        payload = event.to_signable_bytes()
        assert isinstance(payload, bytes)
        assert len(payload) > 0
        # Deterministic
        assert payload == event.to_signable_bytes()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_events.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.ltp.gateway_vm.events'`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/events.py`:

```python
"""BridgeEvent — normalized external chain event for gateway processing."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..domain import DOMAIN_EXTERNAL_EVENT, domain_hash


@dataclass(frozen=True)
class BridgeEvent:
    """A normalized bridge event observed on an external chain.

    Frozen so it can be used as a dict key and to prevent mutation
    after construction.
    """

    source_chain_id: int
    bridge_contract: str
    tx_hash: str
    block_number: int
    log_index: int
    event_name: str
    sender: str
    recipient: str
    payload_hash: str
    amount: int
    nonce: int
    timestamp: float

    @property
    def event_id(self) -> str:
        """Deterministic event identifier: H(chain_id || tx_hash || log_index).

        Used as the replay protection key — two events with the same
        event_id are considered duplicates.
        """
        key_bytes = (
            str(self.source_chain_id).encode()
            + b":"
            + self.tx_hash.encode()
            + b":"
            + str(self.log_index).encode()
        )
        return domain_hash(DOMAIN_EXTERNAL_EVENT, key_bytes)

    def to_signable_bytes(self) -> bytes:
        """Canonical byte representation for signing.

        Struct-packed for deterministic serialization. Mirrors the
        CommitmentRecord.signable_payload() pattern.
        """
        return (
            b"LTP-BRIDGE-EVENT-v1\x00"
            + struct.pack(">Q", self.source_chain_id)
            + self.bridge_contract.encode("utf-8")
            + b"\x00"
            + self.tx_hash.encode("utf-8")
            + b"\x00"
            + struct.pack(">Q", self.block_number)
            + struct.pack(">I", self.log_index)
            + self.event_name.encode("utf-8")
            + b"\x00"
            + self.sender.encode("utf-8")
            + b"\x00"
            + self.recipient.encode("utf-8")
            + b"\x00"
            + self.payload_hash.encode("utf-8")
            + b"\x00"
            + struct.pack(">Q", self.amount)
            + struct.pack(">Q", self.nonce)
            + struct.pack(">d", self.timestamp)
        )
```

Update `src/ltp/gateway_vm/__init__.py`:

```python
"""Gateway VM — POA attestation gateway for GSX devnet."""

from .config import GatewayVMConfig
from .events import BridgeEvent

__all__ = ["GatewayVMConfig", "BridgeEvent"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_events.py -v`

Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/gateway_vm/events.py src/ltp/gateway_vm/__init__.py tests/test_gateway_vm_events.py
git commit -m "feat(gateway-vm): add BridgeEvent data type with deterministic event_id"
```

---

## Task 4: Replay Protection DB

**Files:**
- Create: `src/ltp/gateway_vm/replay.py`
- Test: `tests/test_gateway_vm_replay.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_vm_replay.py`:

```python
"""Tests for ReplayDB — SQLite-backed event deduplication."""

import os
import tempfile
import pytest


class TestReplayDB:
    def test_mark_and_check(self):
        from src.ltp.gateway_vm.replay import ReplayDB

        db = ReplayDB(":memory:")
        assert db.is_processed("event-1") is False
        db.mark_processed("event-1", tx_hash="0xabc", block_number=100)
        assert db.is_processed("event-1") is True

    def test_duplicate_mark_is_idempotent(self):
        from src.ltp.gateway_vm.replay import ReplayDB

        db = ReplayDB(":memory:")
        db.mark_processed("event-1", tx_hash="0xabc", block_number=100)
        # Second mark should not raise
        db.mark_processed("event-1", tx_hash="0xabc", block_number=100)
        assert db.is_processed("event-1") is True

    def test_different_events_independent(self):
        from src.ltp.gateway_vm.replay import ReplayDB

        db = ReplayDB(":memory:")
        db.mark_processed("event-1", tx_hash="0xabc", block_number=100)
        assert db.is_processed("event-2") is False

    def test_persists_to_disk(self):
        from src.ltp.gateway_vm.replay import ReplayDB

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db1 = ReplayDB(path)
            db1.mark_processed("event-1", tx_hash="0xabc", block_number=100)
            db1.close()

            db2 = ReplayDB(path)
            assert db2.is_processed("event-1") is True
            db2.close()
        finally:
            os.unlink(path)

    def test_count(self):
        from src.ltp.gateway_vm.replay import ReplayDB

        db = ReplayDB(":memory:")
        assert db.count() == 0
        db.mark_processed("event-1", tx_hash="0xabc", block_number=100)
        db.mark_processed("event-2", tx_hash="0xdef", block_number=101)
        assert db.count() == 2

    def test_get_record(self):
        from src.ltp.gateway_vm.replay import ReplayDB

        db = ReplayDB(":memory:")
        db.mark_processed("event-1", tx_hash="0xabc", block_number=100)
        record = db.get("event-1")
        assert record is not None
        assert record["tx_hash"] == "0xabc"
        assert record["block_number"] == 100

    def test_get_missing_returns_none(self):
        from src.ltp.gateway_vm.replay import ReplayDB

        db = ReplayDB(":memory:")
        assert db.get("nonexistent") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_replay.py -v`

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/replay.py`:

```python
"""ReplayDB — SQLite-backed event deduplication for the gateway VM."""

from __future__ import annotations

import sqlite3
import time
from typing import Optional


class ReplayDB:
    """Per-source-chain event deduplication using SQLite.

    Each processed event is recorded by its event_id (deterministic hash
    of chain_id + tx_hash + log_index). Duplicate events are rejected
    before any commitment is created.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id    TEXT PRIMARY KEY,
                tx_hash     TEXT NOT NULL,
                block_number INTEGER NOT NULL,
                processed_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def is_processed(self, event_id: str) -> bool:
        """Check if an event has already been processed."""
        row = self._conn.execute(
            "SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return row is not None

    def mark_processed(
        self, event_id: str, *, tx_hash: str, block_number: int
    ) -> None:
        """Record an event as processed. Idempotent — duplicates are ignored."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO processed_events
                (event_id, tx_hash, block_number, processed_at)
            VALUES (?, ?, ?, ?)
            """,
            (event_id, tx_hash, block_number, time.time()),
        )
        self._conn.commit()

    def get(self, event_id: str) -> Optional[dict]:
        """Fetch a processed event record, or None if not found."""
        row = self._conn.execute(
            "SELECT event_id, tx_hash, block_number, processed_at "
            "FROM processed_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "event_id": row[0],
            "tx_hash": row[1],
            "block_number": row[2],
            "processed_at": row[3],
        }

    def count(self) -> int:
        """Return the number of processed events."""
        row = self._conn.execute("SELECT COUNT(*) FROM processed_events").fetchone()
        return row[0]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_replay.py -v`

Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/gateway_vm/replay.py tests/test_gateway_vm_replay.py
git commit -m "feat(gateway-vm): add ReplayDB with SQLite-backed event deduplication"
```

---

## Task 5: Event Validator (12-Point Checklist)

**Files:**
- Create: `src/ltp/gateway_vm/validator.py`
- Test: `tests/test_gateway_vm_validator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_vm_validator.py`:

```python
"""Tests for EventValidator — 12-point validation checklist."""

import pytest


def _make_valid_event():
    from src.ltp.gateway_vm.events import BridgeEvent

    return BridgeEvent(
        source_chain_id=84532,
        bridge_contract="0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
        tx_hash="0xabc123def456",
        block_number=100,
        log_index=0,
        event_name="AnchorCreated",
        sender="0xdeadbeef",
        recipient="0xcafebabe",
        payload_hash="sha3-256:abcd1234",
        amount=100_000_000,
        nonce=1,
        timestamp=1700000000.0,
    )


def _make_validator(*, current_block=200, signer_authorized=True):
    from src.ltp.gateway_vm.config import GatewayVMConfig
    from src.ltp.gateway_vm.replay import ReplayDB
    from src.ltp.gateway_vm.validator import EventValidator

    config = GatewayVMConfig(
        source_chain_id=84532,
        source_bridge_contract="0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
        finality_depth=12,
        dest_chain_id=103115120,
    )
    replay_db = ReplayDB(":memory:")
    return EventValidator(
        config=config,
        replay_db=replay_db,
        get_block_number=lambda: current_block,
        is_signer_authorized=lambda: signer_authorized,
    )


class TestValidEventPasses:
    def test_valid_event_passes_all_checks(self):
        v = _make_validator(current_block=200)
        event = _make_valid_event()
        ok, reason = v.validate(event)
        assert ok is True, f"Expected pass, got: {reason}"
        assert reason == ""


class TestChainIdCheck:
    def test_wrong_source_chain_rejected(self):
        from src.ltp.gateway_vm.events import BridgeEvent

        v = _make_validator()
        event = BridgeEvent(
            source_chain_id=1,  # wrong — expected 84532
            bridge_contract="0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
            tx_hash="0xabc", block_number=100, log_index=0,
            event_name="AnchorCreated", sender="0xaa", recipient="0xbb",
            payload_hash="sha3-256:ff", amount=0, nonce=0, timestamp=1700000000.0,
        )
        ok, reason = v.validate(event)
        assert ok is False
        assert "chain" in reason.lower()


class TestBridgeContractCheck:
    def test_wrong_bridge_contract_rejected(self):
        from src.ltp.gateway_vm.events import BridgeEvent

        v = _make_validator()
        event = BridgeEvent(
            source_chain_id=84532,
            bridge_contract="0xUNAUTHORIZED",  # wrong contract
            tx_hash="0xabc", block_number=100, log_index=0,
            event_name="AnchorCreated", sender="0xaa", recipient="0xbb",
            payload_hash="sha3-256:ff", amount=0, nonce=0, timestamp=1700000000.0,
        )
        ok, reason = v.validate(event)
        assert ok is False
        assert "contract" in reason.lower()


class TestFinalityCheck:
    def test_insufficient_finality_rejected(self):
        v = _make_validator(current_block=105)  # only 5 blocks deep, need 12
        event = _make_valid_event()  # block 100
        ok, reason = v.validate(event)
        assert ok is False
        assert "finality" in reason.lower()

    def test_exact_finality_passes(self):
        v = _make_validator(current_block=112)  # exactly 12 blocks deep
        event = _make_valid_event()  # block 100
        ok, reason = v.validate(event)
        assert ok is True


class TestReplayCheck:
    def test_already_processed_rejected(self):
        v = _make_validator()
        event = _make_valid_event()
        # Pre-mark as processed
        v._replay_db.mark_processed(event.event_id, tx_hash=event.tx_hash, block_number=event.block_number)
        ok, reason = v.validate(event)
        assert ok is False
        assert "replay" in reason.lower()


class TestSignerAuthCheck:
    def test_unauthorized_signer_rejected(self):
        v = _make_validator(signer_authorized=False)
        event = _make_valid_event()
        ok, reason = v.validate(event)
        assert ok is False
        assert "signer" in reason.lower() or "authorized" in reason.lower()


class TestPayloadHashCheck:
    def test_empty_payload_hash_rejected(self):
        from src.ltp.gateway_vm.events import BridgeEvent

        v = _make_validator()
        event = BridgeEvent(
            source_chain_id=84532,
            bridge_contract="0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
            tx_hash="0xabc", block_number=100, log_index=0,
            event_name="AnchorCreated", sender="0xaa", recipient="0xbb",
            payload_hash="",  # empty — invalid
            amount=0, nonce=0, timestamp=1700000000.0,
        )
        ok, reason = v.validate(event)
        assert ok is False
        assert "payload" in reason.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_validator.py -v`

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/validator.py`:

```python
"""EventValidator — 12-point validation checklist for bridge events."""

from __future__ import annotations

from typing import Callable, Optional

from .config import GatewayVMConfig
from .events import BridgeEvent
from .replay import ReplayDB


class EventValidator:
    """Validates bridge events against the gateway's 12-point checklist.

    Each check returns early on failure with a reason string. All checks
    must pass for the event to be accepted.

    Injectable dependencies (get_block_number, is_signer_authorized) allow
    deterministic testing without real RPC connections.
    """

    def __init__(
        self,
        config: GatewayVMConfig,
        replay_db: ReplayDB,
        get_block_number: Callable[[], int],
        is_signer_authorized: Callable[[], bool],
    ) -> None:
        self._config = config
        self._replay_db = replay_db
        self._get_block_number = get_block_number
        self._is_signer_authorized = is_signer_authorized

    def validate(self, event: BridgeEvent) -> tuple[bool, str]:
        """Run all validation checks. Returns (True, "") or (False, reason)."""

        # 1. Source chain ID matches expected
        if event.source_chain_id != self._config.source_chain_id:
            return False, (
                f"chain mismatch: expected {self._config.source_chain_id}, "
                f"got {event.source_chain_id}"
            )

        # 2. Bridge contract address is authorized
        if event.bridge_contract.lower() != self._config.source_bridge_contract.lower():
            return False, (
                f"unauthorized contract: expected "
                f"{self._config.source_bridge_contract}, got {event.bridge_contract}"
            )

        # 3. Event name is non-empty (ABI signature check placeholder)
        if not event.event_name:
            return False, "empty event name"

        # 4. Transaction hash is non-empty
        if not event.tx_hash:
            return False, "empty transaction hash"

        # 5-6. Finality depth threshold met
        current_block = self._get_block_number()
        depth = current_block - event.block_number
        if depth < self._config.finality_depth:
            return False, (
                f"insufficient finality: depth {depth}, "
                f"required {self._config.finality_depth}"
            )

        # 7. Replay status — event not already processed
        if self._replay_db.is_processed(event.event_id):
            return False, f"replay: event {event.event_id[:32]}... already processed"

        # 8. Authorized signer status
        if not self._is_signer_authorized():
            return False, "gateway signer not authorized on devnet"

        # 9. Payload hash is well-formed
        if not event.payload_hash:
            return False, "empty payload hash"

        # 10. Payload hash format check (must be algo-prefixed)
        if ":" not in event.payload_hash:
            return False, f"malformed payload hash: missing algo prefix in {event.payload_hash}"

        # 11. Source/destination routing (sender and recipient non-empty)
        if not event.sender or not event.recipient:
            return False, "missing sender or recipient address"

        # 12. Block number sanity (must be positive)
        if event.block_number <= 0:
            return False, f"invalid block number: {event.block_number}"

        return True, ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_validator.py -v`

Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/gateway_vm/validator.py tests/test_gateway_vm_validator.py
git commit -m "feat(gateway-vm): add EventValidator with 12-point checklist"
```

---

## Task 6: Attestation Writer

**Files:**
- Create: `src/ltp/gateway_vm/writer.py`
- Test: `tests/test_gateway_vm_writer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_vm_writer.py`:

```python
"""Tests for AttestationWriter — commitment creation + ML-DSA-65 signing."""

import pytest
from src.ltp.keypair import KeyPair


@pytest.fixture(scope="module")
def gateway_keypair():
    return KeyPair.generate("gateway-writer-test")


def _make_event():
    from src.ltp.gateway_vm.events import BridgeEvent

    return BridgeEvent(
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


class TestAttestationWriter:
    def test_create_attestation(self, gateway_keypair):
        from src.ltp.gateway_vm.writer import AttestationWriter

        writer = AttestationWriter(
            operator_keypair=gateway_keypair,
            dest_chain_id=103115120,
        )
        event = _make_event()
        attestation = writer.create_attestation(event)

        assert attestation.event_id == event.event_id
        assert attestation.source_chain_id == 84532
        assert attestation.dest_chain_id == 103115120
        assert len(attestation.signature) > 0
        assert len(attestation.digest) > 0

    def test_attestation_signature_verifies(self, gateway_keypair):
        from src.ltp.gateway_vm.writer import AttestationWriter

        writer = AttestationWriter(
            operator_keypair=gateway_keypair,
            dest_chain_id=103115120,
        )
        event = _make_event()
        attestation = writer.create_attestation(event)
        assert attestation.verify(gateway_keypair.vk) is True

    def test_attestation_signature_rejects_wrong_key(self, gateway_keypair):
        from src.ltp.gateway_vm.writer import AttestationWriter

        writer = AttestationWriter(
            operator_keypair=gateway_keypair,
            dest_chain_id=103115120,
        )
        event = _make_event()
        attestation = writer.create_attestation(event)

        wrong_kp = KeyPair.generate("wrong-key")
        assert attestation.verify(wrong_kp.vk) is False

    def test_attestation_is_deterministic_for_same_event(self, gateway_keypair):
        from src.ltp.gateway_vm.writer import AttestationWriter

        writer = AttestationWriter(
            operator_keypair=gateway_keypair,
            dest_chain_id=103115120,
        )
        event = _make_event()
        a1 = writer.create_attestation(event)
        a2 = writer.create_attestation(event)
        # Digest is deterministic (same event → same digest)
        assert a1.digest == a2.digest
        # Signatures may differ (ML-DSA is randomized) but both verify
        assert a1.verify(gateway_keypair.vk)
        assert a2.verify(gateway_keypair.vk)

    def test_requires_keypair(self):
        from src.ltp.gateway_vm.writer import AttestationWriter

        with pytest.raises(TypeError):
            AttestationWriter(operator_keypair=None, dest_chain_id=103115120)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_writer.py -v`

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/writer.py`:

```python
"""AttestationWriter — creates LTP attestation records for bridge events."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import (
    DOMAIN_GATEWAY_ATTEST,
    domain_hash_bytes,
    domain_sign,
    domain_verify,
    signer_fingerprint,
)
from ..keypair import KeyPair
from .events import BridgeEvent


@dataclass(frozen=True)
class GatewayAttestation:
    """A signed attestation that a bridge event was observed and validated.

    The gateway operator signs: DOMAIN_GATEWAY_ATTEST || event.to_signable_bytes()
    The digest is: H(DOMAIN_GATEWAY_ATTEST, event.to_signable_bytes())
    """

    event_id: str
    source_chain_id: int
    dest_chain_id: int
    digest: bytes
    signer_vk_fingerprint: bytes
    signature: bytes
    event_bytes: bytes

    def verify(self, vk: bytes) -> bool:
        """Verify the attestation signature against a verification key."""
        return domain_verify(DOMAIN_GATEWAY_ATTEST, vk, self.event_bytes, self.signature)


class AttestationWriter:
    """Creates ML-DSA-65 signed attestation records for validated bridge events.

    Each attestation binds: the event data, the gateway operator's identity,
    and the destination chain — signed under the DOMAIN_GATEWAY_ATTEST domain.
    """

    def __init__(
        self,
        operator_keypair: KeyPair,
        dest_chain_id: int,
    ) -> None:
        if operator_keypair is None:
            raise TypeError("operator_keypair is required — attestations must be signed")
        self._keypair = operator_keypair
        self._dest_chain_id = dest_chain_id
        self._vk_fingerprint = signer_fingerprint(operator_keypair.vk)

    def create_attestation(self, event: BridgeEvent) -> GatewayAttestation:
        """Create a signed attestation for a validated bridge event."""
        event_bytes = event.to_signable_bytes()
        digest = domain_hash_bytes(DOMAIN_GATEWAY_ATTEST, event_bytes)
        signature = domain_sign(DOMAIN_GATEWAY_ATTEST, self._keypair.sk, event_bytes)

        return GatewayAttestation(
            event_id=event.event_id,
            source_chain_id=event.source_chain_id,
            dest_chain_id=self._dest_chain_id,
            digest=digest,
            signer_vk_fingerprint=self._vk_fingerprint,
            signature=signature,
            event_bytes=event_bytes,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_writer.py -v`

Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/gateway_vm/writer.py tests/test_gateway_vm_writer.py
git commit -m "feat(gateway-vm): add AttestationWriter with ML-DSA-65 domain-separated signing"
```

---

## Task 7: Event Listener (Mock RPC)

**Files:**
- Create: `src/ltp/gateway_vm/listener.py`
- Test: `tests/test_gateway_vm_listener.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_vm_listener.py`:

```python
"""Tests for EventListener — polls external chain for bridge events."""

import pytest


def _make_raw_log(tx_hash="0xabc", block_number=100, log_index=0):
    """Simulate a raw EVM event log dict as returned by web3 or RPC."""
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


class TestEventListener:
    def test_poll_returns_bridge_events(self):
        from src.ltp.gateway_vm.listener import EventListener

        raw_logs = [_make_raw_log("0xaaa", 100, 0), _make_raw_log("0xbbb", 101, 0)]
        listener = EventListener(
            source_chain_id=84532,
            fetch_logs=lambda from_block, to_block: raw_logs,
            get_block_number=lambda: 200,
        )
        events = listener.poll()
        assert len(events) == 2
        assert events[0].tx_hash == "0xaaa"
        assert events[1].tx_hash == "0xbbb"
        assert events[0].source_chain_id == 84532

    def test_poll_advances_cursor(self):
        from src.ltp.gateway_vm.listener import EventListener

        call_args = []

        def mock_fetch(from_block, to_block):
            call_args.append((from_block, to_block))
            return [_make_raw_log(block_number=from_block)]

        listener = EventListener(
            source_chain_id=84532,
            fetch_logs=mock_fetch,
            get_block_number=lambda: 200,
            start_block=50,
        )
        # First poll: from_block=50
        listener.poll()
        assert call_args[0][0] == 50
        # Second poll: cursor advanced past first result
        listener.poll()
        assert call_args[1][0] == 51  # advanced past block 50

    def test_poll_empty_on_no_events(self):
        from src.ltp.gateway_vm.listener import EventListener

        listener = EventListener(
            source_chain_id=84532,
            fetch_logs=lambda fb, tb: [],
            get_block_number=lambda: 200,
        )
        events = listener.poll()
        assert events == []

    def test_poll_respects_current_block(self):
        from src.ltp.gateway_vm.listener import EventListener

        call_args = []

        def mock_fetch(from_block, to_block):
            call_args.append((from_block, to_block))
            return []

        listener = EventListener(
            source_chain_id=84532,
            fetch_logs=mock_fetch,
            get_block_number=lambda: 150,
            start_block=50,
        )
        listener.poll()
        assert call_args[0][1] == 150  # to_block = current head

    def test_missing_args_default_to_empty(self):
        from src.ltp.gateway_vm.listener import EventListener

        raw = {
            "transactionHash": "0xfff",
            "blockNumber": 99,
            "logIndex": 0,
            "address": "0x5083",
            "event": "AnchorCreated",
            "args": {},  # no args
        }
        listener = EventListener(
            source_chain_id=84532,
            fetch_logs=lambda fb, tb: [raw],
            get_block_number=lambda: 200,
        )
        events = listener.poll()
        assert len(events) == 1
        assert events[0].sender == ""
        assert events[0].amount == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_listener.py -v`

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/listener.py`:

```python
"""EventListener — polls external chain for bridge events."""

from __future__ import annotations

import time
from typing import Callable

from .events import BridgeEvent


class EventListener:
    """Polls an external chain for bridge contract events.

    Uses injectable fetch_logs and get_block_number callables so the
    listener can be tested without a real RPC connection. In production,
    these are wired to web3.py event filter calls.
    """

    def __init__(
        self,
        source_chain_id: int,
        fetch_logs: Callable[[int, int], list[dict]],
        get_block_number: Callable[[], int],
        start_block: int = 0,
    ) -> None:
        self._source_chain_id = source_chain_id
        self._fetch_logs = fetch_logs
        self._get_block_number = get_block_number
        self._cursor = start_block

    @property
    def cursor(self) -> int:
        """Current block cursor position."""
        return self._cursor

    def poll(self) -> list[BridgeEvent]:
        """Fetch new bridge events since the last poll.

        Returns a list of BridgeEvent objects. Advances the internal
        cursor past the highest block seen.
        """
        current_block = self._get_block_number()
        if self._cursor > current_block:
            return []

        raw_logs = self._fetch_logs(self._cursor, current_block)
        events = []
        max_block = self._cursor

        for log in raw_logs:
            args = log.get("args", {})
            block_num = log.get("blockNumber", 0)
            event = BridgeEvent(
                source_chain_id=self._source_chain_id,
                bridge_contract=log.get("address", ""),
                tx_hash=log.get("transactionHash", ""),
                block_number=block_num,
                log_index=log.get("logIndex", 0),
                event_name=log.get("event", ""),
                sender=args.get("sender", ""),
                recipient=args.get("recipient", ""),
                payload_hash=args.get("payloadHash", ""),
                amount=args.get("amount", 0),
                nonce=args.get("nonce", 0),
                timestamp=time.time(),
            )
            events.append(event)
            if block_num > max_block:
                max_block = block_num

        # Advance cursor past the highest block processed
        if events:
            self._cursor = max_block + 1
        return events
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_listener.py -v`

Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/gateway_vm/listener.py tests/test_gateway_vm_listener.py
git commit -m "feat(gateway-vm): add EventListener with injectable RPC for testability"
```

---

## Task 8: Gateway Metrics

**Files:**
- Create: `src/ltp/gateway_vm/metrics.py`
- Test: `tests/test_gateway_vm_metrics.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_vm_metrics.py`:

```python
"""Tests for gateway VM metrics registration."""

import pytest


class TestGatewayMetrics:
    def test_create_registers_all_metrics(self):
        from src.ltp.observability.metrics import MetricsRegistry
        from src.ltp.gateway_vm.metrics import create_gateway_metrics

        registry = MetricsRegistry()
        metrics = create_gateway_metrics(registry)

        assert "etp_gateway_events_observed" in metrics
        assert "etp_gateway_events_accepted" in metrics
        assert "etp_gateway_events_rejected" in metrics
        assert "etp_gateway_anchor_latency" in metrics
        assert "etp_gateway_finality_wait" in metrics
        assert "etp_gateway_replay_rejections" in metrics

    def test_counters_increment(self):
        from src.ltp.observability.metrics import MetricsRegistry
        from src.ltp.gateway_vm.metrics import create_gateway_metrics

        registry = MetricsRegistry()
        m = create_gateway_metrics(registry)
        m["etp_gateway_events_observed"].inc()
        m["etp_gateway_events_observed"].inc()
        assert m["etp_gateway_events_observed"].get() == 2.0

    def test_rejected_counter_accepts_labels(self):
        from src.ltp.observability.metrics import MetricsRegistry
        from src.ltp.gateway_vm.metrics import create_gateway_metrics

        registry = MetricsRegistry()
        m = create_gateway_metrics(registry)
        m["etp_gateway_events_rejected"].inc(labels={"reason": "replay"})
        m["etp_gateway_events_rejected"].inc(labels={"reason": "finality"})
        assert m["etp_gateway_events_rejected"].get(labels={"reason": "replay"}) == 1.0
        assert m["etp_gateway_events_rejected"].get(labels={"reason": "finality"}) == 1.0

    def test_histogram_observes(self):
        from src.ltp.observability.metrics import MetricsRegistry
        from src.ltp.gateway_vm.metrics import create_gateway_metrics

        registry = MetricsRegistry()
        m = create_gateway_metrics(registry)
        m["etp_gateway_anchor_latency"].observe(0.5)
        m["etp_gateway_anchor_latency"].observe(1.2)
        # Histogram exists and accepted observations
        assert registry.get("etp_gateway_anchor_latency") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_metrics.py -v`

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/metrics.py`:

```python
"""Gateway VM metrics — Prometheus-compatible counters and histograms."""

from __future__ import annotations

from ..observability.metrics import MetricsRegistry


def create_gateway_metrics(registry: MetricsRegistry) -> dict:
    """Register all gateway VM metrics and return a lookup dict.

    Follows the pattern from create_etp_metrics() in observability/metrics.py.
    """
    return {
        "etp_gateway_events_observed": registry.counter(
            "etp_gateway_events_observed",
            "Total bridge events observed on source chain",
        ),
        "etp_gateway_events_accepted": registry.counter(
            "etp_gateway_events_accepted",
            "Events that passed all validation checks",
        ),
        "etp_gateway_events_rejected": registry.counter(
            "etp_gateway_events_rejected",
            "Events that failed validation (labeled by reason)",
        ),
        "etp_gateway_anchor_latency": registry.histogram(
            "etp_gateway_anchor_latency",
            "Seconds from event observation to devnet anchor",
        ),
        "etp_gateway_finality_wait": registry.histogram(
            "etp_gateway_finality_wait",
            "Seconds spent waiting for source chain finality",
        ),
        "etp_gateway_replay_rejections": registry.counter(
            "etp_gateway_replay_rejections",
            "Replay attempts detected and rejected",
        ),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_metrics.py -v`

Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/gateway_vm/metrics.py tests/test_gateway_vm_metrics.py
git commit -m "feat(gateway-vm): add Prometheus metrics for event observation and anchoring"
```

---

## Task 9: GatewayVMService (Daemon Integration)

**Files:**
- Create: `src/ltp/gateway_vm/service.py`
- Test: `tests/test_gateway_vm_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_vm_service.py`:

```python
"""Tests for GatewayVMService — full daemon tick loop integration."""

import pytest
from unittest.mock import MagicMock
from src.ltp.keypair import KeyPair


@pytest.fixture(scope="module")
def gateway_kp():
    return KeyPair.generate("gateway-service-test")


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


def _make_service(gateway_kp, *, raw_logs=None, current_block=200,
                  anchor_fn=None, signer_authorized=True):
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

    if raw_logs is None:
        raw_logs = []

    if anchor_fn is None:
        anchor_fn = MagicMock(return_value="0xtxhash")

    return GatewayVMService(
        config=config,
        operator_keypair=gateway_kp,
        fetch_logs=lambda fb, tb: raw_logs,
        get_source_block_number=lambda: current_block,
        get_dest_block_number=lambda: 999,
        anchor_fn=anchor_fn,
        is_signer_authorized=lambda: signer_authorized,
    )


class TestTickNoEvents:
    def test_tick_with_no_events(self, gateway_kp):
        svc = _make_service(gateway_kp, raw_logs=[])
        result = svc.tick()
        assert result.epoch == 1
        assert result.events_observed == 0
        assert result.events_accepted == 0
        assert result.events_rejected == 0
        assert result.error == ""


class TestTickProcessesEvent:
    def test_tick_processes_valid_event(self, gateway_kp):
        logs = [_make_raw_log("0xaaa", 100, 0)]
        anchor_fn = MagicMock(return_value="0xtxhash")
        svc = _make_service(gateway_kp, raw_logs=logs, current_block=200,
                            anchor_fn=anchor_fn)
        result = svc.tick()
        assert result.events_observed == 1
        assert result.events_accepted == 1
        assert result.events_rejected == 0
        assert anchor_fn.call_count == 1


class TestReplayRejection:
    def test_same_event_rejected_on_second_tick(self, gateway_kp):
        logs = [_make_raw_log("0xaaa", 100, 0)]
        anchor_fn = MagicMock(return_value="0xtxhash")
        svc = _make_service(gateway_kp, raw_logs=logs, current_block=200,
                            anchor_fn=anchor_fn)
        # First tick: processes event
        r1 = svc.tick()
        assert r1.events_accepted == 1
        # Second tick: same event, rejected as replay
        r2 = svc.tick()
        assert r2.events_observed == 1
        assert r2.events_accepted == 0
        assert r2.events_rejected == 1


class TestFinalityRejection:
    def test_event_rejected_before_finality(self, gateway_kp):
        logs = [_make_raw_log("0xaaa", 195, 0)]  # block 195
        svc = _make_service(gateway_kp, raw_logs=logs, current_block=200)
        # depth = 200 - 195 = 5, need 12
        result = svc.tick()
        assert result.events_rejected == 1
        assert result.events_accepted == 0


class TestAnchorFailure:
    def test_anchor_failure_recorded(self, gateway_kp):
        logs = [_make_raw_log("0xaaa", 100, 0)]
        anchor_fn = MagicMock(side_effect=RuntimeError("RPC timeout"))
        svc = _make_service(gateway_kp, raw_logs=logs, current_block=200,
                            anchor_fn=anchor_fn)
        result = svc.tick()
        assert result.events_observed == 1
        assert result.events_accepted == 0
        assert result.anchor_failures == 1


class TestRetryQueue:
    def test_failed_anchor_retried_next_tick(self, gateway_kp):
        logs = [_make_raw_log("0xaaa", 100, 0)]
        call_count = {"n": 0}

        def failing_then_succeeding(attestation):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("temporary failure")
            return "0xtxhash"

        svc = _make_service(gateway_kp, raw_logs=logs, current_block=200,
                            anchor_fn=failing_then_succeeding)
        # First tick: fails
        r1 = svc.tick()
        assert r1.anchor_failures == 1
        assert svc.retry_queue_size == 1
        # Second tick: retries and succeeds
        r2 = svc.tick()
        assert r2.retries_attempted == 1
        assert svc.retry_queue_size == 0


class TestMultipleEvents:
    def test_multiple_events_in_one_tick(self, gateway_kp):
        logs = [
            _make_raw_log("0xaaa", 100, 0),
            _make_raw_log("0xbbb", 101, 0),
            _make_raw_log("0xccc", 102, 0),
        ]
        anchor_fn = MagicMock(return_value="0xtxhash")
        svc = _make_service(gateway_kp, raw_logs=logs, current_block=200,
                            anchor_fn=anchor_fn)
        result = svc.tick()
        assert result.events_observed == 3
        assert result.events_accepted == 3
        assert anchor_fn.call_count == 3


class TestServiceProperties:
    def test_epoch_increments(self, gateway_kp):
        svc = _make_service(gateway_kp)
        assert svc.epoch == 0
        svc.tick()
        assert svc.epoch == 1
        svc.tick()
        assert svc.epoch == 2

    def test_running_property(self, gateway_kp):
        svc = _make_service(gateway_kp)
        assert svc.running is False

    def test_requires_keypair(self):
        from src.ltp.gateway_vm.config import GatewayVMConfig
        from src.ltp.gateway_vm.service import GatewayVMService

        with pytest.raises(TypeError):
            GatewayVMService(
                config=GatewayVMConfig(),
                operator_keypair=None,
                fetch_logs=lambda fb, tb: [],
                get_source_block_number=lambda: 0,
                get_dest_block_number=lambda: 0,
                anchor_fn=lambda a: "0x",
                is_signer_authorized=lambda: True,
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_service.py -v`

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/service.py`:

```python
"""GatewayVMService — POA attestation gateway daemon."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..keypair import KeyPair
from ..observability.logging import StructuredLogger
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
```

- [ ] **Step 4: Update __init__.py exports**

Update `src/ltp/gateway_vm/__init__.py`:

```python
"""Gateway VM — POA attestation gateway for GSX devnet."""

from .config import GatewayVMConfig
from .events import BridgeEvent
from .listener import EventListener
from .replay import ReplayDB
from .service import GatewayVMService, GatewayVMTickResult
from .validator import EventValidator
from .writer import AttestationWriter, GatewayAttestation

__all__ = [
    "GatewayVMConfig",
    "BridgeEvent",
    "EventListener",
    "ReplayDB",
    "GatewayVMService",
    "GatewayVMTickResult",
    "EventValidator",
    "AttestationWriter",
    "GatewayAttestation",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_service.py -v`

Expected: PASS (all 10 tests)

- [ ] **Step 6: Run all gateway VM tests for regression**

Run: `pytest tests/test_gateway_vm_*.py -v`

Expected: All tests PASS across all 7 test files (~40 tests)

- [ ] **Step 7: Commit**

```bash
git add src/ltp/gateway_vm/service.py src/ltp/gateway_vm/__init__.py tests/test_gateway_vm_service.py
git commit -m "feat(gateway-vm): add GatewayVMService daemon with tick loop, retry queue, and full integration"
```

---

## Task 10: Full Suite Regression + Final Commit

**Files:**
- All `src/ltp/gateway_vm/` files
- All `tests/test_gateway_vm_*.py` files

- [ ] **Step 1: Run the entire test suite**

Run: `pytest tests/ -v --tb=short 2>&1 | tail -30`

Expected: All existing tests PASS. No regressions. New gateway VM tests all PASS.

- [ ] **Step 2: Verify module imports cleanly**

Run: `python -c "from src.ltp.gateway_vm import GatewayVMService, GatewayVMConfig, BridgeEvent, EventValidator, AttestationWriter, ReplayDB, EventListener; print('All imports OK')"`

Expected: `All imports OK`

- [ ] **Step 3: Verify domain tag collision check passes**

Run: `python -c "from src.ltp.domain import DOMAIN_GATEWAY_ATTEST, DOMAIN_EXTERNAL_EVENT; print('Domain tags OK:', DOMAIN_GATEWAY_ATTEST, DOMAIN_EXTERNAL_EVENT)"`

Expected: `Domain tags OK: b'GSX-LTP:gateway-attest:v1\x00' b'GSX-LTP:external-event:v1\x00'`

- [ ] **Step 4: Count new files and lines**

Run: `find src/ltp/gateway_vm -name '*.py' | wc -l && wc -l src/ltp/gateway_vm/*.py`

Expected: 8 files, approximately 400-500 lines total.

Run: `find tests -name 'test_gateway_vm_*.py' | wc -l && wc -l tests/test_gateway_vm_*.py`

Expected: 7 test files, approximately 400-500 lines total.

- [ ] **Step 5: Final commit (if any uncommitted changes)**

```bash
git status
# If clean: nothing to do
# If changes: git add -A src/ltp/gateway_vm/ tests/test_gateway_vm_*.py && git commit -m "chore(gateway-vm): final cleanup for Phase 1 core module"
```

---

---

## Addendum: Audit Gap Tasks (Tasks 11-14)

Post-audit review identified 3 missing and 4 partial spec requirements. Tasks 11-14 close these gaps before Phase 2 begins. Tasks 11-14 depend on Tasks 1-10 being complete.

### Updated File Structure (additions)

| File | Responsibility |
|---|---|
| `src/ltp/gateway_vm/finality.py` | `FinalityWatcher` — confirmation depth tracking extending AnchorVerifier pattern |
| `src/ltp/gateway_vm/main.py` | Entry point — startup ordering, signal handling, reverse-shutdown |
| `tests/test_gateway_vm_finality.py` | Finality watcher tests |
| `tests/test_gateway_vm_main.py` | Entry point lifecycle tests |

---

## Task 11: Finality Watcher (extends AnchorVerifier pattern)

**Audit gap:** Spec requires `finality.py` extending AnchorVerifier's confirmation depth pattern. Plan 1 embedded finality as 2 lines of arithmetic in the validator — insufficient for reorg detection and two-phase verification.

**Files:**
- Create: `src/ltp/gateway_vm/finality.py`
- Modify: `src/ltp/gateway_vm/validator.py` (delegate finality check to FinalityWatcher)
- Test: `tests/test_gateway_vm_finality.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_vm_finality.py`:

```python
"""Tests for FinalityWatcher — confirmation depth tracking."""

import pytest


class TestFinalityWatcher:
    def test_event_below_finality_depth_not_final(self):
        from src.ltp.gateway_vm.finality import FinalityWatcher

        watcher = FinalityWatcher(
            finality_depth=12,
            get_block_number=lambda: 110,
        )
        assert watcher.is_final(block_number=100) is True  # depth=10... wait, 110-100=10 < 12
        # Correction: 110 - 100 = 10 < 12
        assert watcher.is_final(block_number=100) is False

    def test_event_at_exact_finality_is_final(self):
        from src.ltp.gateway_vm.finality import FinalityWatcher

        watcher = FinalityWatcher(
            finality_depth=12,
            get_block_number=lambda: 112,
        )
        assert watcher.is_final(block_number=100) is True

    def test_event_above_finality_is_final(self):
        from src.ltp.gateway_vm.finality import FinalityWatcher

        watcher = FinalityWatcher(
            finality_depth=12,
            get_block_number=lambda: 200,
        )
        assert watcher.is_final(block_number=100) is True

    def test_depth_returns_confirmation_count(self):
        from src.ltp.gateway_vm.finality import FinalityWatcher

        watcher = FinalityWatcher(
            finality_depth=12,
            get_block_number=lambda: 150,
        )
        assert watcher.depth(block_number=100) == 50

    def test_negative_depth_detected_as_reorg(self):
        from src.ltp.gateway_vm.finality import FinalityWatcher

        watcher = FinalityWatcher(
            finality_depth=12,
            get_block_number=lambda: 95,
        )
        # Block 100 at chain head 95 means reorg
        assert watcher.depth(block_number=100) == -5
        assert watcher.is_final(block_number=100) is False

    def test_check_returns_status_tuple(self):
        from src.ltp.gateway_vm.finality import FinalityWatcher

        watcher = FinalityWatcher(
            finality_depth=12,
            get_block_number=lambda: 200,
        )
        is_final, depth, required = watcher.check(block_number=100)
        assert is_final is True
        assert depth == 100
        assert required == 12

    def test_check_insufficient_depth(self):
        from src.ltp.gateway_vm.finality import FinalityWatcher

        watcher = FinalityWatcher(
            finality_depth=12,
            get_block_number=lambda: 105,
        )
        is_final, depth, required = watcher.check(block_number=100)
        assert is_final is False
        assert depth == 5
        assert required == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_finality.py -v`

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/finality.py`:

```python
"""FinalityWatcher — confirmation depth tracking for bridge events.

Extends the AnchorVerifier pattern: two-phase verification (depth check
then finality threshold) with reorg detection (negative depth).
"""

from __future__ import annotations

from typing import Callable


class FinalityWatcher:
    """Tracks source chain confirmation depth for bridge events.

    Mirrors the AnchorVerifier.tick() two-phase model:
      Phase 1: Is the event confirmed? (block exists in chain)
      Phase 2: Has it reached finality depth? (enough blocks on top)

    Reorg detection: if current_block < event_block, the event's block
    was removed from the canonical chain.
    """

    def __init__(
        self,
        finality_depth: int,
        get_block_number: Callable[[], int],
    ) -> None:
        self._finality_depth = finality_depth
        self._get_block_number = get_block_number

    def depth(self, block_number: int) -> int:
        """Current confirmation depth. Negative means reorg."""
        return self._get_block_number() - block_number

    def is_final(self, block_number: int) -> bool:
        """True if event block has reached finality depth."""
        return self.depth(block_number) >= self._finality_depth

    def check(self, block_number: int) -> tuple[bool, int, int]:
        """Full status check. Returns (is_final, current_depth, required_depth)."""
        d = self.depth(block_number)
        return d >= self._finality_depth, d, self._finality_depth
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_finality.py -v`

Expected: PASS (all 7 tests)

- [ ] **Step 5: Update validator to delegate finality to FinalityWatcher**

In `src/ltp/gateway_vm/validator.py`, replace the inline finality check:

Change the constructor to accept a `FinalityWatcher`:

```python
from .finality import FinalityWatcher

class EventValidator:
    def __init__(
        self,
        config: GatewayVMConfig,
        replay_db: ReplayDB,
        get_block_number: Callable[[], int],
        is_signer_authorized: Callable[[], bool],
        finality_watcher: FinalityWatcher | None = None,
    ) -> None:
        self._config = config
        self._replay_db = replay_db
        self._get_block_number = get_block_number
        self._is_signer_authorized = is_signer_authorized
        self._finality = finality_watcher or FinalityWatcher(
            finality_depth=config.finality_depth,
            get_block_number=get_block_number,
        )
```

Replace checks 5-6 in `validate()`:

```python
        # 5-6. Finality depth threshold met (delegates to FinalityWatcher)
        is_final, depth, required = self._finality.check(event.block_number)
        if not is_final:
            if depth < 0:
                return False, f"reorg detected: event block {event.block_number} is {abs(depth)} blocks ahead of chain head"
            return False, (
                f"insufficient finality: depth {depth}, "
                f"required {required}"
            )
```

- [ ] **Step 6: Run existing validator tests for regression**

Run: `pytest tests/test_gateway_vm_validator.py tests/test_gateway_vm_finality.py -v`

Expected: All tests PASS (existing validator tests unchanged because FinalityWatcher auto-constructs from same params)

- [ ] **Step 7: Commit**

```bash
git add src/ltp/gateway_vm/finality.py src/ltp/gateway_vm/validator.py tests/test_gateway_vm_finality.py
git commit -m "feat(gateway-vm): add FinalityWatcher with reorg detection, delegate from validator"
```

---

## Task 12: ChallengeManager Integration

**Audit gap:** Spec requires ChallengeManager wired into the gateway for optimistic mode challenge tracking. Plan 1 had the config field but never instantiated the class.

**Files:**
- Modify: `src/ltp/gateway_vm/service.py` (wire ChallengeManager into tick loop)
- Modify: `src/ltp/gateway_vm/validator.py` (add challenge window status check)
- Test: `tests/test_gateway_vm_service.py` (add challenge integration tests)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gateway_vm_service.py`:

```python
class TestChallengeIntegration:
    def test_optimistic_mode_opens_challenge_window(self, gateway_kp):
        logs = [_make_raw_log("0xaaa", 100, 0)]
        anchor_fn = MagicMock(return_value="0xtxhash")
        svc = _make_service(gateway_kp, raw_logs=logs, current_block=200,
                            anchor_fn=anchor_fn)
        result = svc.tick()
        assert result.events_accepted == 1
        # Challenge window should be open for the anchored event
        assert svc.challenge_manager is not None
        stats = svc.challenge_manager.stats()
        assert stats["open"] == 1

    def test_challenge_tick_auto_finalizes(self, gateway_kp):
        from ltp.bridge.challenge import ChallengeManager

        logs = [_make_raw_log("0xaaa", 100, 0)]
        anchor_fn = MagicMock(return_value="0xtxhash")
        t = [1000.0]
        svc = _make_service(gateway_kp, raw_logs=logs, current_block=200,
                            anchor_fn=anchor_fn,
                            challenge_period=60.0, clock=lambda: t[0])
        svc.tick()
        assert svc.challenge_manager.stats()["open"] == 1
        # Advance past challenge period
        t[0] = 1070.0
        svc.tick()  # tick calls challenge_manager.tick() internally
        assert svc.challenge_manager.stats()["finalized"] == 1

    def test_disabled_challenge_mode_skips_manager(self, gateway_kp):
        from src.ltp.gateway_vm.config import GatewayVMConfig
        from src.ltp.gateway_vm.service import GatewayVMService

        config = GatewayVMConfig(
            enabled=True,
            source_chain_id=84532,
            source_bridge_contract="0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
            finality_depth=12,
            dest_chain_id=103115120,
            replay_db_path=":memory:",
            challenge_mode="disabled",
        )
        svc = GatewayVMService(
            config=config,
            operator_keypair=gateway_kp,
            fetch_logs=lambda fb, tb: [],
            get_source_block_number=lambda: 200,
            get_dest_block_number=lambda: 999,
            anchor_fn=MagicMock(return_value="0x"),
            is_signer_authorized=lambda: True,
        )
        assert svc.challenge_manager is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_service.py::TestChallengeIntegration -v`

Expected: FAIL — `GatewayVMService` has no `challenge_manager` attribute

- [ ] **Step 3: Wire ChallengeManager into service.py**

In `src/ltp/gateway_vm/service.py`, add to imports:

```python
from ..bridge.challenge import ChallengeManager
```

In `__init__`, after the metrics setup:

```python
        # Challenge manager (optimistic mode only)
        self._challenge_manager: ChallengeManager | None = None
        if config.challenge_mode == "optimistic":
            self._challenge_manager = ChallengeManager(
                challenge_period=config.challenge_period_seconds,
                clock=clock,
            )
```

Add constructor parameter `clock: Callable[[], float] | None = None` to `__init__`.

Add property:

```python
    @property
    def challenge_manager(self) -> ChallengeManager | None:
        return self._challenge_manager
```

In `tick()`, after successful anchor in `_try_anchor`, add:

```python
        # Open challenge window for optimistic mode
        if self._challenge_manager is not None:
            self._challenge_manager.open_challenge_window(
                event.event_id, attestation.digest[:32]
            )
```

At the start of `tick()`, before processing the retry queue:

```python
            # Tick challenge manager to auto-finalize expired windows
            if self._challenge_manager is not None:
                self._challenge_manager.tick()
```

Update `_make_service` helper in tests to pass through `challenge_period` and `clock` params.

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_gateway_vm_service.py -v`

Expected: All tests PASS (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/gateway_vm/service.py tests/test_gateway_vm_service.py
git commit -m "feat(gateway-vm): wire ChallengeManager for optimistic mode challenge tracking"
```

---

## Task 13: StructuredLogger + CorrelationContext

**Audit gap:** Spec requires structured JSON logging with per-event correlation IDs for audit trail. Plan 1 imported StructuredLogger but used standard `logging.getLogger` instead.

**Files:**
- Modify: `src/ltp/gateway_vm/service.py` (replace standard logger with StructuredLogger)
- Test: `tests/test_gateway_vm_service.py` (verify correlation IDs in log output)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gateway_vm_service.py`:

```python
import logging


class _CapturingHandler(logging.Handler):
    """Captures log records for assertion."""
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


class TestStructuredLogging:
    def test_tick_produces_correlation_id(self, gateway_kp):
        from src.ltp.gateway_vm.service import GatewayVMService

        logs = [_make_raw_log("0xaaa", 100, 0)]
        anchor_fn = MagicMock(return_value="0xtxhash")
        svc = _make_service(gateway_kp, raw_logs=logs, current_block=200,
                            anchor_fn=anchor_fn)

        handler = _CapturingHandler()
        svc._log.attach_handler(handler)

        svc.tick()

        # At least one log record should have a correlation_id
        assert len(handler.records) > 0
        has_correlation = any(
            hasattr(r, "_extra_fields") and "correlation_id" in str(r._extra_fields)
            for r in handler.records
        )
        assert has_correlation, "Expected correlation_id in structured log output"

    def test_each_tick_gets_unique_correlation_id(self, gateway_kp):
        from src.ltp.gateway_vm.service import GatewayVMService

        svc = _make_service(gateway_kp, raw_logs=[])
        handler = _CapturingHandler()
        svc._log.attach_handler(handler)

        svc.tick()
        svc.tick()

        # Extract correlation IDs from records
        cids = set()
        for r in handler.records:
            if hasattr(r, "_extra_fields"):
                cid = r._extra_fields.get("correlation_id")
                if cid:
                    cids.add(cid)
        assert len(cids) >= 2, "Each tick should produce a unique correlation ID"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_service.py::TestStructuredLogging -v`

Expected: FAIL — `svc._log` does not exist or is not a `StructuredLogger`

- [ ] **Step 3: Replace standard logger with StructuredLogger**

In `src/ltp/gateway_vm/service.py`, replace:

```python
import logging
```
```python
logger = logging.getLogger(__name__)
```

With:

```python
from ..observability.logging import StructuredLogger, CorrelationContext
```

In `__init__`, add:

```python
        self._log = StructuredLogger(
            f"etp.gateway.{config.gateway_id}",
            default_fields={"gateway_id": config.gateway_id},
        )
```

In `tick()`, wrap the entire tick body in a correlation scope:

```python
    def tick(self) -> GatewayVMTickResult:
        with self._epoch_lock:
            self._epoch += 1
        result = GatewayVMTickResult(epoch=self._epoch)

        with self._log.correlation_scope() as cid:
            with self._data_lock:
                # ... existing tick body ...
                # Replace all logger.info/warning/error calls with self._log.info/warning/error
                # Add event_id=, epoch=, etc. as kwargs for structured output
```

Replace each `logger.info(...)` / `logger.warning(...)` / `logger.error(...)` with the equivalent `self._log.info(...)` call, passing structured fields as kwargs instead of string formatting:

```python
# Before:
logger.info("Gateway: rejected event %s: %s", event.tx_hash[:16], reason)
# After:
self._log.info("event rejected", event_id=event.event_id[:32], tx_hash=event.tx_hash[:16], reason=reason)
```

```python
# Before:
logger.warning("Gateway: anchor failed for %s: %s", event.tx_hash[:16], exc)
# After:
self._log.warning("anchor failed", event_id=event.event_id[:32], tx_hash=event.tx_hash[:16], error=str(exc))
```

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_gateway_vm_service.py -v`

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/ltp/gateway_vm/service.py tests/test_gateway_vm_service.py
git commit -m "feat(gateway-vm): replace stdlib logger with StructuredLogger + correlation IDs"
```

---

## Task 14: Gateway Entry Point (main.py)

**Audit gap:** Spec requires `main.py` with unified startup, shutdown, OS signal handling, and reverse-startup teardown following the ETPNode.stop() pattern.

**Files:**
- Create: `src/ltp/gateway_vm/main.py`
- Test: `tests/test_gateway_vm_main.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gateway_vm_main.py`:

```python
"""Tests for GatewayVM entry point — lifecycle, signal handling, teardown."""

import signal
import pytest
from unittest.mock import MagicMock, patch
from src.ltp.keypair import KeyPair


@pytest.fixture(scope="module")
def gateway_kp():
    return KeyPair.generate("gateway-main-test")


class TestGatewayVMLifecycle:
    def test_start_and_stop(self, gateway_kp):
        from src.ltp.gateway_vm.main import GatewayVM
        from src.ltp.gateway_vm.config import GatewayVMConfig

        config = GatewayVMConfig(
            enabled=True,
            source_chain_id=84532,
            source_bridge_contract="0x5083",
            dest_chain_id=103115120,
            replay_db_path=":memory:",
        )
        vm = GatewayVM(
            config=config,
            operator_keypair=gateway_kp,
            fetch_logs=lambda fb, tb: [],
            get_source_block_number=lambda: 200,
            get_dest_block_number=lambda: 999,
            anchor_fn=MagicMock(return_value="0x"),
            is_signer_authorized=lambda: True,
        )
        vm.start()
        assert vm.running is True
        vm.stop()
        assert vm.running is False

    def test_stop_is_idempotent(self, gateway_kp):
        from src.ltp.gateway_vm.main import GatewayVM
        from src.ltp.gateway_vm.config import GatewayVMConfig

        config = GatewayVMConfig(
            enabled=True,
            replay_db_path=":memory:",
        )
        vm = GatewayVM(
            config=config,
            operator_keypair=gateway_kp,
            fetch_logs=lambda fb, tb: [],
            get_source_block_number=lambda: 0,
            get_dest_block_number=lambda: 0,
            anchor_fn=MagicMock(return_value="0x"),
            is_signer_authorized=lambda: True,
        )
        vm.start()
        vm.stop()
        vm.stop()  # second stop should not raise
        assert vm.running is False

    def test_teardown_order_is_reverse_startup(self, gateway_kp):
        from src.ltp.gateway_vm.main import GatewayVM
        from src.ltp.gateway_vm.config import GatewayVMConfig

        config = GatewayVMConfig(
            enabled=True,
            replay_db_path=":memory:",
        )
        teardown_order = []

        vm = GatewayVM(
            config=config,
            operator_keypair=gateway_kp,
            fetch_logs=lambda fb, tb: [],
            get_source_block_number=lambda: 0,
            get_dest_block_number=lambda: 0,
            anchor_fn=MagicMock(return_value="0x"),
            is_signer_authorized=lambda: True,
        )
        vm.start()

        # Patch stop methods to record teardown order
        original_svc_stop = vm._service.stop
        original_db_close = vm._replay_db.close

        def mock_svc_stop():
            teardown_order.append("service")
            original_svc_stop()

        def mock_db_close():
            teardown_order.append("replay_db")
            original_db_close()

        vm._service.stop = mock_svc_stop
        vm._replay_db.close = mock_db_close

        vm.stop()
        # Service stops before replay DB closes (reverse of startup)
        assert teardown_order == ["service", "replay_db"]

    def test_signal_handler_triggers_stop(self, gateway_kp):
        from src.ltp.gateway_vm.main import GatewayVM
        from src.ltp.gateway_vm.config import GatewayVMConfig

        config = GatewayVMConfig(
            enabled=True,
            replay_db_path=":memory:",
        )
        vm = GatewayVM(
            config=config,
            operator_keypair=gateway_kp,
            fetch_logs=lambda fb, tb: [],
            get_source_block_number=lambda: 0,
            get_dest_block_number=lambda: 0,
            anchor_fn=MagicMock(return_value="0x"),
            is_signer_authorized=lambda: True,
        )
        vm.start()
        assert vm.running is True
        # Simulate SIGTERM
        vm._signal_handler(signal.SIGTERM, None)
        assert vm.running is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_vm_main.py -v`

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/ltp/gateway_vm/main.py`:

```python
"""GatewayVM — unified entry point with lifecycle management.

Follows the ETPNode pattern: ordered startup, signal registration,
reverse-startup teardown on shutdown.

Startup order:
  1. ReplayDB (persistence layer)
  2. GatewayVMService (daemon process, depends on ReplayDB)
  3. Signal handlers (registered after service is running)

Teardown order (reverse):
  1. Service.stop() (daemon thread joins)
  2. ReplayDB.close() (persistence flushed)
"""

from __future__ import annotations

import signal
from typing import Callable, Optional

from ..keypair import KeyPair
from ..observability.logging import StructuredLogger
from .config import GatewayVMConfig
from .replay import ReplayDB
from .service import GatewayVMService
from .writer import GatewayAttestation


class GatewayVM:
    """Unified gateway VM process with lifecycle management.

    Manages startup ordering, OS signal handling, and reverse-startup
    teardown. Wraps GatewayVMService with operational concerns.
    """

    def __init__(
        self,
        config: GatewayVMConfig,
        operator_keypair: KeyPair,
        fetch_logs: Callable[[int, int], list[dict]],
        get_source_block_number: Callable[[], int],
        get_dest_block_number: Callable[[], int],
        anchor_fn: Callable[[GatewayAttestation], str],
        is_signer_authorized: Callable[[], bool],
    ) -> None:
        self._config = config
        self._log = StructuredLogger(
            f"etp.gateway-vm.{config.gateway_id}",
            default_fields={"gateway_id": config.gateway_id},
        )
        self._running = False

        # --- Startup order 1: Persistence ---
        self._replay_db = ReplayDB(config.replay_db_path)

        # --- Startup order 2: Service ---
        self._service = GatewayVMService(
            config=config,
            operator_keypair=operator_keypair,
            fetch_logs=fetch_logs,
            get_source_block_number=get_source_block_number,
            get_dest_block_number=get_dest_block_number,
            anchor_fn=anchor_fn,
            is_signer_authorized=is_signer_authorized,
        )

        self._prev_sigterm = None
        self._prev_sigint = None

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start the gateway VM in order: replay DB -> service -> signals."""
        if self._running:
            return

        self._log.info("starting gateway VM", mode=self._config.mode)

        # Step 2: Start service daemon
        self._service.start()

        # Step 3: Register signal handlers
        self._prev_sigterm = signal.getsignal(signal.SIGTERM)
        self._prev_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        self._running = True
        self._log.info("gateway VM started",
                       source_chain=self._config.source_chain_id,
                       dest_chain=self._config.dest_chain_id)

    def stop(self) -> None:
        """Stop the gateway VM in reverse startup order."""
        if not self._running:
            return

        self._log.info("stopping gateway VM", epoch=self._service.epoch)
        self._running = False

        # Reverse step 3: Restore signal handlers
        if self._prev_sigterm is not None:
            signal.signal(signal.SIGTERM, self._prev_sigterm)
            self._prev_sigterm = None
        if self._prev_sigint is not None:
            signal.signal(signal.SIGINT, self._prev_sigint)
            self._prev_sigint = None

        # Reverse step 2: Stop service
        try:
            self._service.stop()
        except Exception as exc:
            self._log.error("error stopping service", error=str(exc))

        # Reverse step 1: Close replay DB
        try:
            self._replay_db.close()
        except Exception as exc:
            self._log.error("error closing replay DB", error=str(exc))

        self._log.info("gateway VM stopped")

    def _signal_handler(self, signum, frame) -> None:
        """Handle SIGTERM/SIGINT by triggering orderly shutdown."""
        self._log.info("received signal, shutting down", signal=signum)
        self.stop()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_vm_main.py -v`

Expected: PASS (all 4 tests)

- [ ] **Step 5: Update __init__.py exports**

Add to `src/ltp/gateway_vm/__init__.py`:

```python
from .main import GatewayVM
from .finality import FinalityWatcher
```

And to `__all__`:

```python
    "GatewayVM",
    "FinalityWatcher",
```

- [ ] **Step 6: Run full gateway VM test suite**

Run: `pytest tests/test_gateway_vm_*.py -v`

Expected: All tests PASS (~60 tests across 9 test files)

- [ ] **Step 7: Commit**

```bash
git add src/ltp/gateway_vm/main.py src/ltp/gateway_vm/__init__.py tests/test_gateway_vm_main.py
git commit -m "feat(gateway-vm): add GatewayVM entry point with signal handling and reverse-startup teardown"
```

---

## Summary

| Task | Component | Files | Tests |
|---|---|---|---|
| 1 | Domain tags | `domain.py` (modify) | 2 new assertions |
| 2 | Config | `config.py`, `__init__.py` | 3 tests |
| 3 | BridgeEvent | `events.py` | 4 tests |
| 4 | Replay DB | `replay.py` | 7 tests |
| 5 | Validator | `validator.py` | 8 tests |
| 6 | Attestation Writer | `writer.py` | 5 tests |
| 7 | Event Listener | `listener.py` | 5 tests |
| 8 | Metrics | `metrics.py` | 4 tests |
| 9 | Service (daemon) | `service.py` | 10 tests |
| 10 | Regression | All | Full suite |
| 11 | Finality Watcher | `finality.py`, `validator.py` (modify) | 7 tests |
| 12 | ChallengeManager | `service.py` (modify) | 3 tests |
| 13 | StructuredLogger | `service.py` (modify) | 2 tests |
| 14 | Entry Point | `main.py` | 4 tests |

**Total: ~10 new files, ~1,100 lines of code, ~62 tests, 14 commits.**

Each task is independently testable. Each commit produces a green test suite. The service (Task 9) wires all components together — if any earlier task has a bug, the service integration tests catch it. Addendum tasks (11-14) close spec gaps found during audit.

**Deferred to Plan 2 (justified):**
- AnchorClient extension for devnet submission — Plan 2 builds the full transaction flow with rate limiting and circuit breaker. The opaque `anchor_fn` callable is the correct seam; Plan 2 replaces it with a real `DevnetAnchorClient`.
- SequenceTracker integration with ReplayDB — Plan 2 wires on-chain state, making monotonic sequence enforcement meaningful. Standalone `ReplayDB` is correct for Plan 1's scope.
- ReplayDB `source_chain_id` column — Plan 2 introduces multi-source support; Plan 1 is single-source.

**What this plan does NOT cover (separate plans):**
- Phase 2: REST endpoints, DevnetAnchorClient, multi-source chains, e2e integration, Grafana dashboards
- Phase 3: 15 stress test scenarios under adversarial conditions
- Phase 4: MoveVM integration, BLS attestation, writer permissioning
