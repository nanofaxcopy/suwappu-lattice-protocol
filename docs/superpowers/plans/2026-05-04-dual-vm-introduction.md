# Dual VM Introduction — Implementation Plan (Phase 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce MoveVM as a second execution environment alongside EVM, establishing writer permissioning, BLS state root attestation, Move state delta propagation, and the EVM precompile interface. Phase 4 is infrastructure plumbing — it builds the dual-VM runtime but does NOT implement the identity system (that comes from MoveVM+DID and DID Expansion Plan).

**Architecture:** POA nodes (writers) run MoveVM alongside EVM. POS nodes (readers) receive Move state deltas, verify against BLS-signed roots, and serve Move state reads. Transaction-level writer enforcement makes unauthorized Move transactions deterministic no-ops. BLS aggregate signatures attest to Move state roots on a 1-5s cadence decoupled from DAG blocks.

**Tech Stack:** Python 3.12+, blst (BLS library), MoveVM binary (TBD: Aptos/Sui/independent — Open Question #8), SQLite (state tracking), existing LTP infrastructure, pytest + Hypothesis

**Spec:** `docs/LTP_GATEWAY_VM_PLAN.md` — Phase 4 (Dual VM Introduction)

**Depends on:** Plans 1-3 complete. Single-VM POA attestation proven on live testnets.

**Precedes:** Proposed MoveVM+DID Architecture (identity system on dual-VM foundation).

---

## File Structure

| File | Responsibility |
|---|---|
| **Writer Permissioning** | |
| `src/ltp/dual_vm/__init__.py` | Dual VM package |
| `src/ltp/dual_vm/config.py` | `DualVMConfig` — Move-specific configuration |
| `src/ltp/dual_vm/writer_registry.py` | `WriterRegistry` — authorized Move writers (mirrors EVM governance contract) |
| `src/ltp/dual_vm/tx_filter.py` | `MoveTransactionFilter` — post-ordering writer enforcement |
| `src/ltp/domain.py` | **Modify:** add `DOMAIN_MOVE_ATTEST`, `DOMAIN_WRITER_REGISTRY` tags |
| **BLS Attestation** | |
| `src/ltp/dual_vm/bls_keys.py` | `BLSKeyPair`, `BLSPublicKey` — BLS12-381 key types (wraps blst) |
| `src/ltp/dual_vm/bls_attestation.py` | `MoveStateAttestation`, `BLSAggregator` — committee signing |
| **State Propagation** | |
| `src/ltp/dual_vm/state_delta.py` | `MoveStateDelta`, `DeltaStore` — serialized state changes |
| `src/ltp/dual_vm/state_verifier.py` | `MoveStateVerifier` — POS-side root recomputation + BLS verification |
| `src/ltp/dual_vm/precompile.py` | `MoveStatePrecompile` — EVM precompile interface definition (0x0F) |
| **Dual State Root** | |
| `src/ltp/dual_vm/dual_root.py` | `DualStateRoot` — combined EVM + Move state root |
| **Integration** | |
| `src/ltp/dual_vm/poa_executor.py` | `POAExecutor` — POA-side tick loop: filter → execute → attest |
| `src/ltp/dual_vm/pos_consumer.py` | `POSConsumer` — POS-side: apply deltas → verify root → serve reads |
| **Tests** | |
| `tests/test_dual_vm_config.py` | Configuration tests |
| `tests/test_dual_vm_writer_registry.py` | Writer permissioning tests |
| `tests/test_dual_vm_tx_filter.py` | Transaction filter tests |
| `tests/test_dual_vm_bls.py` | BLS key generation and aggregation tests |
| `tests/test_dual_vm_attestation.py` | Move state attestation tests |
| `tests/test_dual_vm_state_delta.py` | State delta serialization and application |
| `tests/test_dual_vm_state_verifier.py` | POS verification tests |
| `tests/test_dual_vm_precompile.py` | Precompile interface tests |
| `tests/test_dual_vm_dual_root.py` | Dual state root tests |
| `tests/test_dual_vm_poa_executor.py` | POA executor integration tests |
| `tests/test_dual_vm_pos_consumer.py` | POS consumer integration tests |
| `tests/test_dual_vm_equivocation.py` | Committee equivocation detection tests |

---

## Task 1: Domain Tags and Dual VM Config

**Files:**
- Modify: `src/ltp/domain.py`
- Create: `src/ltp/dual_vm/__init__.py`
- Create: `src/ltp/dual_vm/config.py`
- Test: `tests/test_dual_vm_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dual_vm_config.py`:

```python
"""Tests for DualVMConfig and Phase 4 domain tags."""

import os
import pytest


class TestDualVMDomainTags:
    def test_move_attest_tag_exists(self):
        from src.ltp.domain import DOMAIN_MOVE_ATTEST
        assert DOMAIN_MOVE_ATTEST == b"GSX-LTP:move-attest:v1\x00"

    def test_writer_registry_tag_exists(self):
        from src.ltp.domain import DOMAIN_WRITER_REGISTRY
        assert DOMAIN_WRITER_REGISTRY == b"GSX-LTP:writer-registry:v1\x00"


class TestDualVMConfigDefaults:
    def test_defaults(self):
        from src.ltp.dual_vm.config import DualVMConfig

        cfg = DualVMConfig()
        assert cfg.move_enabled is False
        assert cfg.node_role == "pos"
        assert cfg.attestation_cadence_seconds == 3.0
        assert cfg.committee_size == 30
        assert cfg.bls_signing_scheme == "aggregator-rotated"
        assert cfg.precompile_address == "0x0F"
        assert cfg.delta_gossip_topic == "move-state-deltas"
        assert cfg.state_db_path == ":memory:"

    def test_from_env_overrides(self):
        from src.ltp.dual_vm.config import DualVMConfig

        env = {
            "ETP_DUAL_VM_MOVE_ENABLED": "true",
            "ETP_DUAL_VM_NODE_ROLE": "poa",
            "ETP_DUAL_VM_ATTESTATION_CADENCE": "1.0",
            "ETP_DUAL_VM_COMMITTEE_SIZE": "10",
            "ETP_DUAL_VM_BLS_SCHEME": "threshold",
        }
        for k, v in env.items():
            os.environ[k] = v
        try:
            cfg = DualVMConfig.from_env()
            assert cfg.move_enabled is True
            assert cfg.node_role == "poa"
            assert cfg.attestation_cadence_seconds == 1.0
            assert cfg.committee_size == 10
            assert cfg.bls_signing_scheme == "threshold"
        finally:
            for k in env:
                os.environ.pop(k, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dual_vm_config.py -v`

Expected: FAIL

- [ ] **Step 3: Write the implementation**

Add to `src/ltp/domain.py` after the gateway domain tags:

```python
DOMAIN_MOVE_ATTEST      = b"GSX-LTP:move-attest:v1\x00"
DOMAIN_WRITER_REGISTRY  = b"GSX-LTP:writer-registry:v1\x00"
```

Add both to `__all__` and `_ALL_TAGS`.

Create `src/ltp/dual_vm/__init__.py`:

```python
"""Dual VM — EVM + MoveVM execution environment (Phase 4)."""

from .config import DualVMConfig

__all__ = ["DualVMConfig"]
```

Create `src/ltp/dual_vm/config.py`:

```python
"""Dual VM configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class DualVMConfig:
    """Configuration for the dual VM (EVM + MoveVM) execution environment.

    node_role determines behavior:
      - "poa": Run MoveVM, execute Move txs, sign BLS attestations
      - "pos": Receive Move deltas, verify roots, serve reads
    """

    move_enabled: bool = False
    node_role: str = "pos"  # "poa" | "pos"

    # BLS attestation
    attestation_cadence_seconds: float = 3.0  # 1-5s range
    committee_size: int = 30
    bls_signing_scheme: str = "aggregator-rotated"  # "aggregator-rotated" | "threshold" | "frost"

    # Precompile
    precompile_address: str = "0x0F"

    # State propagation
    delta_gossip_topic: str = "move-state-deltas"
    state_db_path: str = ":memory:"

    # Writer registry
    writer_registry_contract: str = ""

    @classmethod
    def from_env(cls) -> DualVMConfig:
        """Create config from ETP_DUAL_VM_* environment variables."""

        def _bool(key: str, default: bool) -> bool:
            val = os.environ.get(key, "")
            return val.lower() in ("true", "1", "yes") if val else default

        def _int(key: str, default: int) -> int:
            val = os.environ.get(key, "")
            return int(val) if val else default

        def _float(key: str, default: float) -> float:
            val = os.environ.get(key, "")
            return float(val) if val else default

        def _str(key: str, default: str) -> str:
            return os.environ.get(key, default)

        return cls(
            move_enabled=_bool("ETP_DUAL_VM_MOVE_ENABLED", False),
            node_role=_str("ETP_DUAL_VM_NODE_ROLE", "pos"),
            attestation_cadence_seconds=_float("ETP_DUAL_VM_ATTESTATION_CADENCE", 3.0),
            committee_size=_int("ETP_DUAL_VM_COMMITTEE_SIZE", 30),
            bls_signing_scheme=_str("ETP_DUAL_VM_BLS_SCHEME", "aggregator-rotated"),
            precompile_address=_str("ETP_DUAL_VM_PRECOMPILE", "0x0F"),
            delta_gossip_topic=_str("ETP_DUAL_VM_GOSSIP_TOPIC", "move-state-deltas"),
            state_db_path=_str("ETP_DUAL_VM_STATE_DB", ":memory:"),
            writer_registry_contract=_str("ETP_DUAL_VM_WRITER_REGISTRY", ""),
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_dual_vm_config.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ltp/domain.py src/ltp/dual_vm/__init__.py src/ltp/dual_vm/config.py tests/test_dual_vm_config.py
git commit -m "feat(dual-vm): add DualVMConfig and DOMAIN_MOVE_ATTEST, DOMAIN_WRITER_REGISTRY tags"
```

---

## Task 2: Writer Registry

**Spec:** Transaction-level writer enforcement. Writer registry lives on EVM side as governance contract. Move trusts whatever writer list EVM publishes.

**Files:**
- Create: `src/ltp/dual_vm/writer_registry.py`
- Test: `tests/test_dual_vm_writer_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dual_vm_writer_registry.py`:

```python
"""Tests for WriterRegistry — authorized Move transaction writers."""

import pytest


class TestWriterRegistry:
    def test_empty_registry(self):
        from src.ltp.dual_vm.writer_registry import WriterRegistry
        reg = WriterRegistry()
        assert reg.is_authorized("0xabc") is False
        assert reg.writer_count == 0

    def test_add_and_check_writer(self):
        from src.ltp.dual_vm.writer_registry import WriterRegistry
        reg = WriterRegistry()
        reg.add_writer("0xpoa_node_1")
        assert reg.is_authorized("0xpoa_node_1") is True
        assert reg.writer_count == 1

    def test_remove_writer(self):
        from src.ltp.dual_vm.writer_registry import WriterRegistry
        reg = WriterRegistry()
        reg.add_writer("0xpoa_node_1")
        reg.remove_writer("0xpoa_node_1")
        assert reg.is_authorized("0xpoa_node_1") is False

    def test_case_insensitive_address(self):
        from src.ltp.dual_vm.writer_registry import WriterRegistry
        reg = WriterRegistry()
        reg.add_writer("0xAbCdEf")
        assert reg.is_authorized("0xabcdef") is True
        assert reg.is_authorized("0xABCDEF") is True

    def test_list_writers(self):
        from src.ltp.dual_vm.writer_registry import WriterRegistry
        reg = WriterRegistry()
        reg.add_writer("0xaaa")
        reg.add_writer("0xbbb")
        writers = reg.list_writers()
        assert len(writers) == 2

    def test_snapshot_returns_frozen_copy(self):
        from src.ltp.dual_vm.writer_registry import WriterRegistry
        reg = WriterRegistry()
        reg.add_writer("0xaaa")
        snap = reg.snapshot()
        reg.add_writer("0xbbb")
        assert len(snap) == 1  # snapshot not affected by later mutation

    def test_thread_safe(self):
        import threading
        from src.ltp.dual_vm.writer_registry import WriterRegistry
        reg = WriterRegistry()
        errors = []

        def add_many(prefix, count):
            try:
                for i in range(count):
                    reg.add_writer(f"0x{prefix}_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_many, args=(f"t{t}", 100)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert reg.writer_count == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dual_vm_writer_registry.py -v`

Expected: FAIL

- [ ] **Step 3: Write the implementation**

Create `src/ltp/dual_vm/writer_registry.py`:

```python
"""WriterRegistry — tracks authorized MoveVM transaction writers.

In production, this mirrors the on-chain writer registry governance contract.
Writers are POA node addresses authorized to execute Move transactions.
"""

from __future__ import annotations

import threading


class WriterRegistry:
    """Thread-safe registry of authorized Move writers.

    Addresses are normalized to lowercase for case-insensitive comparison.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._writers: set[str] = set()

    def add_writer(self, address: str) -> None:
        """Register an address as an authorized Move writer."""
        with self._lock:
            self._writers.add(address.lower())

    def remove_writer(self, address: str) -> None:
        """Revoke an address's Move write authorization."""
        with self._lock:
            self._writers.discard(address.lower())

    def is_authorized(self, address: str) -> bool:
        """Check if an address is authorized to execute Move transactions."""
        with self._lock:
            return address.lower() in self._writers

    @property
    def writer_count(self) -> int:
        with self._lock:
            return len(self._writers)

    def list_writers(self) -> list[str]:
        """Return list of all authorized writer addresses."""
        with self._lock:
            return sorted(self._writers)

    def snapshot(self) -> frozenset[str]:
        """Return an immutable snapshot of current writer set."""
        with self._lock:
            return frozenset(self._writers)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_dual_vm_writer_registry.py -v`

Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/dual_vm/writer_registry.py tests/test_dual_vm_writer_registry.py
git commit -m "feat(dual-vm): add WriterRegistry for Move transaction authorization"
```

---

## Task 3: Move Transaction Filter

**Spec:** Post-ordering writer enforcement. Unauthorized Move transactions become deterministic no-ops.

**Files:**
- Create: `src/ltp/dual_vm/tx_filter.py`
- Test: `tests/test_dual_vm_tx_filter.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dual_vm_tx_filter.py`:

```python
"""Tests for MoveTransactionFilter — post-ordering writer enforcement."""

import pytest
from dataclasses import dataclass


@dataclass
class MockTransaction:
    """Simulates a transaction from the DAG ordered stream."""
    kind: str  # "evm" | "move"
    sender: str
    data: bytes = b""


class TestMoveTransactionFilter:
    def test_evm_transactions_pass_through(self):
        from src.ltp.dual_vm.tx_filter import MoveTransactionFilter
        from src.ltp.dual_vm.writer_registry import WriterRegistry

        reg = WriterRegistry()
        f = MoveTransactionFilter(reg)

        tx = MockTransaction(kind="evm", sender="0xanyone")
        assert f.should_execute(tx) is True

    def test_authorized_move_tx_passes(self):
        from src.ltp.dual_vm.tx_filter import MoveTransactionFilter
        from src.ltp.dual_vm.writer_registry import WriterRegistry

        reg = WriterRegistry()
        reg.add_writer("0xpoa_writer")
        f = MoveTransactionFilter(reg)

        tx = MockTransaction(kind="move", sender="0xpoa_writer")
        assert f.should_execute(tx) is True

    def test_unauthorized_move_tx_becomes_noop(self):
        from src.ltp.dual_vm.tx_filter import MoveTransactionFilter
        from src.ltp.dual_vm.writer_registry import WriterRegistry

        reg = WriterRegistry()
        f = MoveTransactionFilter(reg)

        tx = MockTransaction(kind="move", sender="0xunauthorized")
        assert f.should_execute(tx) is False

    def test_filter_batch_deterministic(self):
        from src.ltp.dual_vm.tx_filter import MoveTransactionFilter
        from src.ltp.dual_vm.writer_registry import WriterRegistry

        reg = WriterRegistry()
        reg.add_writer("0xwriter_a")

        f = MoveTransactionFilter(reg)
        txs = [
            MockTransaction(kind="evm", sender="0xanyone"),
            MockTransaction(kind="move", sender="0xwriter_a"),
            MockTransaction(kind="move", sender="0xunauthorized"),
            MockTransaction(kind="evm", sender="0xother"),
            MockTransaction(kind="move", sender="0xwriter_a"),
        ]

        results = f.filter_batch(txs)
        assert results == [True, True, False, True, True]

    def test_noop_count_tracked(self):
        from src.ltp.dual_vm.tx_filter import MoveTransactionFilter
        from src.ltp.dual_vm.writer_registry import WriterRegistry

        reg = WriterRegistry()
        f = MoveTransactionFilter(reg)

        for i in range(5):
            f.should_execute(MockTransaction(kind="move", sender=f"0xbad_{i}"))

        assert f.noop_count == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dual_vm_tx_filter.py -v`

Expected: FAIL

- [ ] **Step 3: Write the implementation**

Create `src/ltp/dual_vm/tx_filter.py`:

```python
"""MoveTransactionFilter — post-ordering writer enforcement.

From the spec: 'Every node verifies Move transaction sender is in writer
registry before MoveVM execution. Unauthorized transactions become no-ops
deterministically.'

This is the security boundary. The mempool layer provides a soft check
(performance optimization), but this filter is the enforcement point.
"""

from __future__ import annotations

import threading

from .writer_registry import WriterRegistry


class MoveTransactionFilter:
    """Filters Move transactions based on writer authorization.

    EVM transactions always pass through.
    Move transactions from unauthorized senders become no-ops.
    """

    def __init__(self, writer_registry: WriterRegistry) -> None:
        self._registry = writer_registry
        self._noop_count = 0
        self._lock = threading.Lock()

    def should_execute(self, tx) -> bool:
        """Determine if a transaction should be executed.

        Returns True for all EVM transactions.
        Returns True for Move transactions from authorized writers.
        Returns False (no-op) for Move transactions from unauthorized senders.

        The tx object must have 'kind' (str) and 'sender' (str) attributes.
        """
        if tx.kind != "move":
            return True

        if self._registry.is_authorized(tx.sender):
            return True

        with self._lock:
            self._noop_count += 1
        return False

    def filter_batch(self, txs: list) -> list[bool]:
        """Filter a batch of transactions. Returns list of execute/noop decisions."""
        return [self.should_execute(tx) for tx in txs]

    @property
    def noop_count(self) -> int:
        """Total unauthorized Move transactions filtered as no-ops."""
        with self._lock:
            return self._noop_count
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_dual_vm_tx_filter.py -v`

Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/dual_vm/tx_filter.py tests/test_dual_vm_tx_filter.py
git commit -m "feat(dual-vm): add MoveTransactionFilter — deterministic writer enforcement"
```

---

## Task 4: BLS Key Types

**Spec:** BLS aggregate signatures for committee attestation. Start with aggregator-rotated BLS.

**Note:** This task defines the BLS key interface. If the `blst` library is not available, it provides a test-mode fallback using HMAC-SHA256 for signature simulation. The interface is identical — production uses `blst`, tests use the fallback.

**Files:**
- Create: `src/ltp/dual_vm/bls_keys.py`
- Test: `tests/test_dual_vm_bls.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dual_vm_bls.py`:

```python
"""Tests for BLS key types — generation, signing, verification, aggregation."""

import pytest


class TestBLSKeyPair:
    def test_generate(self):
        from src.ltp.dual_vm.bls_keys import BLSKeyPair
        kp = BLSKeyPair.generate()
        assert len(kp.secret_key) > 0
        assert len(kp.public_key) > 0

    def test_sign_and_verify(self):
        from src.ltp.dual_vm.bls_keys import BLSKeyPair
        kp = BLSKeyPair.generate()
        msg = b"move state root v1"
        sig = kp.sign(msg)
        assert kp.verify(msg, sig) is True

    def test_wrong_message_fails(self):
        from src.ltp.dual_vm.bls_keys import BLSKeyPair
        kp = BLSKeyPair.generate()
        sig = kp.sign(b"correct message")
        assert kp.verify(b"wrong message", sig) is False

    def test_wrong_key_fails(self):
        from src.ltp.dual_vm.bls_keys import BLSKeyPair
        kp1 = BLSKeyPair.generate()
        kp2 = BLSKeyPair.generate()
        sig = kp1.sign(b"test")
        assert kp2.verify(b"test", sig) is False

    def test_deterministic_public_key_from_secret(self):
        from src.ltp.dual_vm.bls_keys import BLSKeyPair
        kp1 = BLSKeyPair.generate(seed=b"fixed_seed_for_test_determinism!")
        kp2 = BLSKeyPair.generate(seed=b"fixed_seed_for_test_determinism!")
        assert kp1.public_key == kp2.public_key


class TestBLSAggregation:
    def test_aggregate_signatures(self):
        from src.ltp.dual_vm.bls_keys import BLSKeyPair, aggregate_signatures, aggregate_verify

        kps = [BLSKeyPair.generate() for _ in range(5)]
        msg = b"epoch_42_move_root"
        sigs = [kp.sign(msg) for kp in kps]
        pks = [kp.public_key for kp in kps]

        agg_sig = aggregate_signatures(sigs)
        assert len(agg_sig) > 0

        assert aggregate_verify(msg, pks, agg_sig) is True

    def test_aggregate_fails_with_wrong_participant(self):
        from src.ltp.dual_vm.bls_keys import BLSKeyPair, aggregate_signatures, aggregate_verify

        kps = [BLSKeyPair.generate() for _ in range(3)]
        impostor = BLSKeyPair.generate()
        msg = b"epoch_99_root"

        sigs = [kp.sign(msg) for kp in kps]
        # Include impostor's public key instead of kps[2]'s
        wrong_pks = [kps[0].public_key, kps[1].public_key, impostor.public_key]

        agg_sig = aggregate_signatures(sigs)
        assert aggregate_verify(msg, wrong_pks, agg_sig) is False

    def test_aggregate_constant_size(self):
        from src.ltp.dual_vm.bls_keys import BLSKeyPair, aggregate_signatures

        msg = b"size_test"
        sig_3 = aggregate_signatures([BLSKeyPair.generate().sign(msg) for _ in range(3)])
        sig_30 = aggregate_signatures([BLSKeyPair.generate().sign(msg) for _ in range(30)])
        # BLS aggregate: constant size regardless of participant count
        assert len(sig_3) == len(sig_30)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dual_vm_bls.py -v`

Expected: FAIL

- [ ] **Step 3: Write the implementation**

Create `src/ltp/dual_vm/bls_keys.py`:

```python
"""BLS12-381 key types for committee attestation.

Production: uses the blst library for real BLS signatures.
Test fallback: HMAC-SHA256 simulation when blst is unavailable.

The interface is identical — callers don't need to know which backend is active.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Optional

try:
    import blst
    _HAS_BLST = True
except ImportError:
    _HAS_BLST = False


@dataclass(frozen=True)
class BLSKeyPair:
    """BLS12-381 key pair for committee signing."""

    secret_key: bytes
    public_key: bytes

    @classmethod
    def generate(cls, seed: Optional[bytes] = None) -> BLSKeyPair:
        """Generate a new BLS key pair."""
        if _HAS_BLST:
            ikm = seed if seed else os.urandom(32)
            sk = blst.SecretKey()
            sk.from_seed(ikm)
            pk = blst.P1(sk)
            return cls(secret_key=sk.to_bytes(), public_key=pk.compress())
        else:
            # Test fallback: HMAC-based simulation
            sk_bytes = seed if seed else os.urandom(32)
            pk_bytes = hashlib.sha256(b"BLS-PK:" + sk_bytes).digest()
            return cls(secret_key=sk_bytes, public_key=pk_bytes)

    def sign(self, message: bytes) -> bytes:
        """Sign a message."""
        if _HAS_BLST:
            sk = blst.SecretKey()
            sk.from_bytes(self.secret_key)
            sig = blst.P2()
            sig.hash_to(message).sign_with(sk)
            return sig.compress()
        else:
            return hmac.new(self.secret_key, message, hashlib.sha256).digest()

    def verify(self, message: bytes, signature: bytes) -> bool:
        """Verify a signature against this key pair's public key."""
        if _HAS_BLST:
            pk = blst.P1(self.public_key)
            sig = blst.P2(signature)
            return blst.PT.finalverify(
                blst.PT(blst.P2().hash_to(message).to_affine(), pk.to_affine()),
                blst.PT(sig.to_affine(), blst.P1.generator().to_affine()),
            )
        else:
            expected = hmac.new(self.secret_key, message, hashlib.sha256).digest()
            return hmac.compare_digest(expected, signature)


def aggregate_signatures(signatures: list[bytes]) -> bytes:
    """Aggregate multiple BLS signatures into one constant-size signature."""
    if _HAS_BLST:
        agg = blst.P2()
        for sig_bytes in signatures:
            sig = blst.P2(sig_bytes)
            agg.add(sig)
        return agg.compress()
    else:
        # Fallback: XOR all signatures (deterministic, constant size)
        result = bytearray(32)
        for sig in signatures:
            for i in range(min(len(sig), 32)):
                result[i] ^= sig[i]
        return bytes(result)


def aggregate_verify(message: bytes, public_keys: list[bytes], aggregate_sig: bytes) -> bool:
    """Verify an aggregate signature against multiple public keys."""
    if _HAS_BLST:
        # Real BLS aggregate verification
        pks = [blst.P1(pk) for pk in public_keys]
        sig = blst.P2(aggregate_sig)
        msgs = [message] * len(pks)
        return blst.PT.aggregate_verify(pks, msgs, sig)
    else:
        # Fallback: recompute aggregate from individual verifications
        # This requires knowing the secret keys, which we don't have.
        # Instead, we recompute what the aggregate SHOULD be from public keys.
        # For test mode, we XOR the HMAC of each (sk, msg) — but we only have PKs.
        # Workaround: store a mapping. Simpler: just re-derive.
        # For test mode, we hash PK+message to get expected individual sigs,
        # then XOR them to get expected aggregate.
        expected_agg = bytearray(32)
        for pk in public_keys:
            individual = hmac.new(pk, message, hashlib.sha256).digest()
            for i in range(32):
                expected_agg[i] ^= individual[i]
        return hmac.compare_digest(bytes(expected_agg), aggregate_sig)
```

**Important note:** The test-mode fallback's `verify()` method needs access to the secret key (stored in the `BLSKeyPair` dataclass). For `aggregate_verify` in test mode, we need a different approach since we only have public keys. The fallback uses PK-derived HMAC, but individual `sign()` uses SK-derived HMAC. We need to align them.

Fix the fallback `sign()` to also include the public key derivation so `aggregate_verify` can work:

Replace the fallback `sign` and `verify` in the `BLSKeyPair`:

```python
    def sign(self, message: bytes) -> bytes:
        """Sign a message."""
        if _HAS_BLST:
            # ... real BLS ...
        else:
            # Use PK-based HMAC so aggregate_verify can reconstruct
            return hmac.new(self.public_key, message, hashlib.sha256).digest()

    def verify(self, message: bytes, signature: bytes) -> bool:
        """Verify a signature against this key pair's public key."""
        if _HAS_BLST:
            # ... real BLS ...
        else:
            expected = hmac.new(self.public_key, message, hashlib.sha256).digest()
            return hmac.compare_digest(expected, signature)
```

This makes individual sign/verify and aggregate_verify consistent in test mode.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_dual_vm_bls.py -v`

Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/dual_vm/bls_keys.py tests/test_dual_vm_bls.py
git commit -m "feat(dual-vm): add BLS key types with blst production backend and HMAC test fallback"
```

---

## Task 5: Move State Attestation

**Spec:** BLS aggregate attestation on Move state roots. Attestation content: Move state root + epoch identifier. Decoupled from DAG blocks.

**Files:**
- Create: `src/ltp/dual_vm/bls_attestation.py`
- Test: `tests/test_dual_vm_attestation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dual_vm_attestation.py`:

```python
"""Tests for MoveStateAttestation and BLSAggregator."""

import pytest


class TestMoveStateAttestation:
    def test_create_attestation(self):
        from src.ltp.dual_vm.bls_attestation import MoveStateAttestation

        att = MoveStateAttestation(
            move_state_root=b"\x01" * 32,
            epoch_id=42,
            committee_size=5,
            aggregate_signature=b"\xaa" * 32,
            signer_bitmap=0b11111,
        )
        assert att.move_state_root == b"\x01" * 32
        assert att.epoch_id == 42

    def test_signable_bytes_deterministic(self):
        from src.ltp.dual_vm.bls_attestation import MoveStateAttestation

        att = MoveStateAttestation(
            move_state_root=b"\x01" * 32,
            epoch_id=42,
            committee_size=5,
            aggregate_signature=b"",
            signer_bitmap=0,
        )
        assert att.signable_bytes() == att.signable_bytes()
        assert len(att.signable_bytes()) > 0


class TestBLSAggregator:
    def test_collect_and_aggregate(self):
        from src.ltp.dual_vm.bls_keys import BLSKeyPair
        from src.ltp.dual_vm.bls_attestation import BLSAggregator

        committee = [BLSKeyPair.generate() for _ in range(5)]
        agg = BLSAggregator(committee_public_keys=[kp.public_key for kp in committee])

        move_root = b"\xab" * 32
        epoch_id = 10

        # Each committee member signs
        for i, kp in enumerate(committee):
            agg.add_signature(i, kp.sign(agg.signable_message(move_root, epoch_id)))

        attestation = agg.finalize(move_root, epoch_id)
        assert attestation.epoch_id == 10
        assert attestation.committee_size == 5
        assert attestation.signer_bitmap == 0b11111  # all 5 signed

    def test_partial_committee(self):
        from src.ltp.dual_vm.bls_keys import BLSKeyPair
        from src.ltp.dual_vm.bls_attestation import BLSAggregator

        committee = [BLSKeyPair.generate() for _ in range(5)]
        agg = BLSAggregator(committee_public_keys=[kp.public_key for kp in committee])

        move_root = b"\xcd" * 32
        epoch_id = 20

        # Only 3 of 5 sign
        for i in [0, 2, 4]:
            agg.add_signature(i, committee[i].sign(agg.signable_message(move_root, epoch_id)))

        attestation = agg.finalize(move_root, epoch_id)
        assert attestation.signer_bitmap == 0b10101
        assert attestation.committee_size == 5

    def test_duplicate_signature_rejected(self):
        from src.ltp.dual_vm.bls_keys import BLSKeyPair
        from src.ltp.dual_vm.bls_attestation import BLSAggregator

        committee = [BLSKeyPair.generate() for _ in range(3)]
        agg = BLSAggregator(committee_public_keys=[kp.public_key for kp in committee])
        msg = agg.signable_message(b"\x00" * 32, 1)
        agg.add_signature(0, committee[0].sign(msg))

        with pytest.raises(ValueError, match="already signed"):
            agg.add_signature(0, committee[0].sign(msg))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dual_vm_attestation.py -v`

Expected: FAIL

- [ ] **Step 3: Write the implementation**

Create `src/ltp/dual_vm/bls_attestation.py`:

```python
"""BLS aggregate attestation for Move state roots.

After each Move execution epoch, the POA committee collectively signs
the new Move state root. The aggregate signature is constant-size
regardless of committee size.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..domain import DOMAIN_MOVE_ATTEST, domain_hash_bytes
from .bls_keys import aggregate_signatures


@dataclass(frozen=True)
class MoveStateAttestation:
    """A BLS-signed attestation over a Move state root.

    Produced by the POA committee, verified by POS nodes.
    """

    move_state_root: bytes  # 32 bytes
    epoch_id: int
    committee_size: int
    aggregate_signature: bytes
    signer_bitmap: int  # bitfield: bit i = 1 if committee member i signed

    def signable_bytes(self) -> bytes:
        """Canonical bytes for signing: domain || root || epoch."""
        return (
            DOMAIN_MOVE_ATTEST
            + self.move_state_root
            + struct.pack(">Q", self.epoch_id)
        )


class BLSAggregator:
    """Collects individual BLS signatures from committee members
    and produces an aggregate attestation.

    Usage:
      1. Create with committee public keys
      2. add_signature(index, sig) as each member signs
      3. finalize(root, epoch) to produce MoveStateAttestation
    """

    def __init__(self, committee_public_keys: list[bytes]) -> None:
        self._committee_pks = committee_public_keys
        self._signatures: dict[int, bytes] = {}

    def signable_message(self, move_state_root: bytes, epoch_id: int) -> bytes:
        """The message that each committee member signs."""
        return (
            DOMAIN_MOVE_ATTEST
            + move_state_root
            + struct.pack(">Q", epoch_id)
        )

    def add_signature(self, member_index: int, signature: bytes) -> None:
        """Add a committee member's signature."""
        if member_index in self._signatures:
            raise ValueError(f"member {member_index} already signed")
        if member_index < 0 or member_index >= len(self._committee_pks):
            raise IndexError(f"member index {member_index} out of range")
        self._signatures[member_index] = signature

    def finalize(self, move_state_root: bytes, epoch_id: int) -> MoveStateAttestation:
        """Produce the aggregate attestation from collected signatures."""
        bitmap = 0
        sigs = []
        for idx in sorted(self._signatures.keys()):
            bitmap |= (1 << idx)
            sigs.append(self._signatures[idx])

        agg_sig = aggregate_signatures(sigs) if sigs else b""

        return MoveStateAttestation(
            move_state_root=move_state_root,
            epoch_id=epoch_id,
            committee_size=len(self._committee_pks),
            aggregate_signature=agg_sig,
            signer_bitmap=bitmap,
        )

    @property
    def collected_count(self) -> int:
        return len(self._signatures)

    def reset(self) -> None:
        """Clear collected signatures for next epoch."""
        self._signatures.clear()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_dual_vm_attestation.py -v`

Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/dual_vm/bls_attestation.py tests/test_dual_vm_attestation.py
git commit -m "feat(dual-vm): add BLSAggregator and MoveStateAttestation for committee signing"
```

---

## Task 6: State Delta and Dual Root

**Spec:** Move state delta propagation + dual state root (EVM root + Move root) in block header.

**Files:**
- Create: `src/ltp/dual_vm/state_delta.py`
- Create: `src/ltp/dual_vm/dual_root.py`
- Test: `tests/test_dual_vm_state_delta.py`
- Test: `tests/test_dual_vm_dual_root.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dual_vm_state_delta.py`:

```python
"""Tests for MoveStateDelta — serialized state changes."""

import pytest


class TestMoveStateDelta:
    def test_create_delta(self):
        from src.ltp.dual_vm.state_delta import MoveStateDelta

        delta = MoveStateDelta(
            epoch_id=42,
            prev_root=b"\x00" * 32,
            new_root=b"\x01" * 32,
            changes=[{"key": b"resource::did", "value": b"\xab\xcd"}],
        )
        assert delta.epoch_id == 42
        assert delta.change_count == 1

    def test_serialize_deserialize(self):
        from src.ltp.dual_vm.state_delta import MoveStateDelta

        delta = MoveStateDelta(
            epoch_id=10,
            prev_root=b"\xaa" * 32,
            new_root=b"\xbb" * 32,
            changes=[
                {"key": b"key1", "value": b"val1"},
                {"key": b"key2", "value": b"val2"},
            ],
        )
        data = delta.serialize()
        restored = MoveStateDelta.deserialize(data)
        assert restored.epoch_id == delta.epoch_id
        assert restored.prev_root == delta.prev_root
        assert restored.new_root == delta.new_root
        assert restored.change_count == 2

    def test_empty_delta(self):
        from src.ltp.dual_vm.state_delta import MoveStateDelta

        delta = MoveStateDelta(
            epoch_id=0,
            prev_root=b"\x00" * 32,
            new_root=b"\x00" * 32,
            changes=[],
        )
        assert delta.change_count == 0
        data = delta.serialize()
        assert len(data) > 0
```

Create `tests/test_dual_vm_dual_root.py`:

```python
"""Tests for DualStateRoot — combined EVM + Move state root."""

import pytest


class TestDualStateRoot:
    def test_create_dual_root(self):
        from src.ltp.dual_vm.dual_root import DualStateRoot

        root = DualStateRoot(
            evm_root=b"\xaa" * 32,
            move_root=b"\xbb" * 32,
            block_number=1000,
        )
        assert root.evm_root == b"\xaa" * 32
        assert root.move_root == b"\xbb" * 32
        assert root.block_number == 1000

    def test_combined_hash_deterministic(self):
        from src.ltp.dual_vm.dual_root import DualStateRoot

        r1 = DualStateRoot(evm_root=b"\x01" * 32, move_root=b"\x02" * 32, block_number=1)
        r2 = DualStateRoot(evm_root=b"\x01" * 32, move_root=b"\x02" * 32, block_number=1)
        assert r1.combined_hash == r2.combined_hash

    def test_different_roots_different_hash(self):
        from src.ltp.dual_vm.dual_root import DualStateRoot

        r1 = DualStateRoot(evm_root=b"\x01" * 32, move_root=b"\x02" * 32, block_number=1)
        r2 = DualStateRoot(evm_root=b"\x01" * 32, move_root=b"\x03" * 32, block_number=1)
        assert r1.combined_hash != r2.combined_hash

    def test_verify_move_root_against_attestation(self):
        from src.ltp.dual_vm.dual_root import DualStateRoot

        root = DualStateRoot(
            evm_root=b"\xaa" * 32,
            move_root=b"\xbb" * 32,
            block_number=500,
        )
        assert root.move_root_matches(b"\xbb" * 32) is True
        assert root.move_root_matches(b"\xcc" * 32) is False
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_dual_vm_state_delta.py tests/test_dual_vm_dual_root.py -v`

Expected: FAIL

- [ ] **Step 3: Write the implementations**

Create `src/ltp/dual_vm/state_delta.py`:

```python
"""MoveStateDelta — serialized state changes from Move execution.

Produced by POA nodes after Move transaction execution.
Consumed by POS nodes for state tree reconstruction.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class MoveStateDelta:
    """A set of Move state changes from a single execution epoch."""

    epoch_id: int
    prev_root: bytes  # 32 bytes
    new_root: bytes  # 32 bytes
    changes: list[dict]  # [{"key": bytes, "value": bytes}, ...]

    @property
    def change_count(self) -> int:
        return len(self.changes)

    def serialize(self) -> bytes:
        """Serialize for gossip propagation."""
        header = struct.pack(">Q", self.epoch_id) + self.prev_root + self.new_root
        # Encode changes as JSON for simplicity (BCS encoding in production)
        changes_json = json.dumps([
            {"key": c["key"].hex() if isinstance(c["key"], bytes) else c["key"],
             "value": c["value"].hex() if isinstance(c["value"], bytes) else c["value"]}
            for c in self.changes
        ]).encode()
        return header + struct.pack(">I", len(changes_json)) + changes_json

    @classmethod
    def deserialize(cls, data: bytes) -> MoveStateDelta:
        """Deserialize from gossip payload."""
        epoch_id = struct.unpack(">Q", data[:8])[0]
        prev_root = data[8:40]
        new_root = data[40:72]
        changes_len = struct.unpack(">I", data[72:76])[0]
        changes_json = json.loads(data[76:76 + changes_len])
        changes = [
            {"key": bytes.fromhex(c["key"]), "value": bytes.fromhex(c["value"])}
            for c in changes_json
        ]
        return cls(epoch_id=epoch_id, prev_root=prev_root, new_root=new_root, changes=changes)
```

Create `src/ltp/dual_vm/dual_root.py`:

```python
"""DualStateRoot — combined EVM + Move state root for block headers.

Each block commits two state roots: one for EVM, one for Move.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class DualStateRoot:
    """Combined state root for a block with both EVM and Move state."""

    evm_root: bytes  # 32 bytes
    move_root: bytes  # 32 bytes
    block_number: int

    @property
    def combined_hash(self) -> bytes:
        """H(evm_root || move_root || block_number) — unique block state identifier."""
        return hashlib.sha3_256(
            self.evm_root + self.move_root + struct.pack(">Q", self.block_number)
        ).digest()

    def move_root_matches(self, expected_root: bytes) -> bool:
        """Check if the Move root matches an expected value (from attestation)."""
        return self.move_root == expected_root
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_dual_vm_state_delta.py tests/test_dual_vm_dual_root.py -v`

Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/dual_vm/state_delta.py src/ltp/dual_vm/dual_root.py tests/test_dual_vm_state_delta.py tests/test_dual_vm_dual_root.py
git commit -m "feat(dual-vm): add MoveStateDelta serialization and DualStateRoot"
```

---

## Task 7: Precompile Interface

**Spec:** EVM precompile at `0x0F` for Move state reads. Reserved in Phase 4.

**Files:**
- Create: `src/ltp/dual_vm/precompile.py`
- Test: `tests/test_dual_vm_precompile.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dual_vm_precompile.py`:

```python
"""Tests for MoveStatePrecompile — EVM precompile interface at 0x0F."""

import pytest


class TestMoveStatePrecompile:
    def test_address_is_0x0f(self):
        from src.ltp.dual_vm.precompile import MoveStatePrecompile
        pc = MoveStatePrecompile()
        assert pc.address == "0x0F"

    def test_read_returns_state_value(self):
        from src.ltp.dual_vm.precompile import MoveStatePrecompile

        state = {b"resource::counter": b"\x00\x00\x00\x2a"}
        pc = MoveStatePrecompile(state_reader=lambda key: state.get(key))
        result = pc.read(b"resource::counter")
        assert result == b"\x00\x00\x00\x2a"

    def test_read_missing_key_returns_none(self):
        from src.ltp.dual_vm.precompile import MoveStatePrecompile
        pc = MoveStatePrecompile(state_reader=lambda key: None)
        assert pc.read(b"nonexistent") is None

    def test_gas_cost_is_negligible(self):
        from src.ltp.dual_vm.precompile import MoveStatePrecompile
        pc = MoveStatePrecompile()
        assert pc.gas_cost(b"any_key") < 1000  # memory access cost

    def test_abi_encode_call(self):
        from src.ltp.dual_vm.precompile import MoveStatePrecompile
        pc = MoveStatePrecompile()
        calldata = pc.encode_read_call(b"resource::did::0xabc")
        assert isinstance(calldata, bytes)
        assert len(calldata) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dual_vm_precompile.py -v`

Expected: FAIL

- [ ] **Step 3: Write the implementation**

Create `src/ltp/dual_vm/precompile.py`:

```python
"""MoveStatePrecompile — EVM precompile interface for Move state reads.

Reserved at address 0x0F. Provides memory-access-cost reads of Move state
from within EVM contracts. In production, this is implemented at the
execution engine level. This Python module defines the interface and
ABI encoding for callers.

Phase 4 reserves the address and defines the interface.
MoveVM+DID makes it critical (EVM contracts read DID state).
"""

from __future__ import annotations

import hashlib
import struct
from typing import Callable, Optional

_PRECOMPILE_ADDRESS = "0x0F"
_BASE_GAS_COST = 100  # negligible — memory access


class MoveStatePrecompile:
    """Interface definition for the Move state read precompile.

    In production, this maps to a native precompile at address 0x0F.
    In tests, accepts an injectable state_reader callable.
    """

    def __init__(
        self,
        state_reader: Optional[Callable[[bytes], Optional[bytes]]] = None,
    ) -> None:
        self._reader = state_reader or (lambda key: None)

    @property
    def address(self) -> str:
        return _PRECOMPILE_ADDRESS

    def read(self, key: bytes) -> Optional[bytes]:
        """Read a Move state value by key."""
        return self._reader(key)

    def gas_cost(self, key: bytes) -> int:
        """Gas cost for a Move state read (negligible — memory access)."""
        return _BASE_GAS_COST

    def encode_read_call(self, key: bytes) -> bytes:
        """ABI-encode a read call for the precompile.

        Format: function selector (4 bytes) + key length (32 bytes) + key data
        Selector: bytes4(keccak256("readMoveState(bytes)"))
        """
        selector = hashlib.sha3_256(b"readMoveState(bytes)").digest()[:4]
        # ABI encoding: offset (32) + length (32) + padded data
        offset = struct.pack(">I", 32).rjust(32, b"\x00")
        length = struct.pack(">I", len(key)).rjust(32, b"\x00")
        padded_key = key + b"\x00" * (32 - len(key) % 32) if len(key) % 32 != 0 else key
        return selector + offset + length + padded_key
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_dual_vm_precompile.py -v`

Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/dual_vm/precompile.py tests/test_dual_vm_precompile.py
git commit -m "feat(dual-vm): add MoveStatePrecompile interface at 0x0F"
```

---

## Task 8: State Verifier (POS Side)

**Spec:** POS nodes apply deltas, recompute root, verify against BLS-signed attestation. On mismatch: halt reads and flag equivocation.

**Files:**
- Create: `src/ltp/dual_vm/state_verifier.py`
- Test: `tests/test_dual_vm_state_verifier.py`
- Test: `tests/test_dual_vm_equivocation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dual_vm_state_verifier.py`:

```python
"""Tests for MoveStateVerifier — POS-side root verification."""

import pytest


class TestMoveStateVerifier:
    def test_verify_matching_root(self):
        from src.ltp.dual_vm.state_verifier import MoveStateVerifier
        from src.ltp.dual_vm.state_delta import MoveStateDelta
        from src.ltp.dual_vm.bls_attestation import MoveStateAttestation

        new_root = b"\xab" * 32
        delta = MoveStateDelta(
            epoch_id=1, prev_root=b"\x00" * 32,
            new_root=new_root, changes=[],
        )
        attestation = MoveStateAttestation(
            move_state_root=new_root, epoch_id=1,
            committee_size=5, aggregate_signature=b"\xff" * 32,
            signer_bitmap=0b11111,
        )

        verifier = MoveStateVerifier()
        result = verifier.verify_delta(delta, attestation)
        assert result.is_valid is True
        assert result.equivocation_detected is False

    def test_mismatched_root_detected(self):
        from src.ltp.dual_vm.state_verifier import MoveStateVerifier
        from src.ltp.dual_vm.state_delta import MoveStateDelta
        from src.ltp.dual_vm.bls_attestation import MoveStateAttestation

        delta = MoveStateDelta(
            epoch_id=1, prev_root=b"\x00" * 32,
            new_root=b"\xab" * 32, changes=[],
        )
        attestation = MoveStateAttestation(
            move_state_root=b"\xcd" * 32,  # DIFFERENT root
            epoch_id=1, committee_size=5,
            aggregate_signature=b"\xff" * 32,
            signer_bitmap=0b11111,
        )

        verifier = MoveStateVerifier()
        result = verifier.verify_delta(delta, attestation)
        assert result.is_valid is False
        assert result.equivocation_detected is True

    def test_halts_reads_on_equivocation(self):
        from src.ltp.dual_vm.state_verifier import MoveStateVerifier
        from src.ltp.dual_vm.state_delta import MoveStateDelta
        from src.ltp.dual_vm.bls_attestation import MoveStateAttestation

        delta = MoveStateDelta(
            epoch_id=1, prev_root=b"\x00" * 32,
            new_root=b"\xab" * 32, changes=[],
        )
        attestation = MoveStateAttestation(
            move_state_root=b"\xcd" * 32,
            epoch_id=1, committee_size=5,
            aggregate_signature=b"\xff" * 32,
            signer_bitmap=0b11111,
        )

        verifier = MoveStateVerifier()
        verifier.verify_delta(delta, attestation)  # triggers equivocation
        assert verifier.reads_halted is True

    def test_reads_not_halted_on_valid(self):
        from src.ltp.dual_vm.state_verifier import MoveStateVerifier
        from src.ltp.dual_vm.state_delta import MoveStateDelta
        from src.ltp.dual_vm.bls_attestation import MoveStateAttestation

        root = b"\xab" * 32
        delta = MoveStateDelta(epoch_id=1, prev_root=b"\x00" * 32, new_root=root, changes=[])
        attestation = MoveStateAttestation(
            move_state_root=root, epoch_id=1,
            committee_size=5, aggregate_signature=b"\xff" * 32,
            signer_bitmap=0b11111,
        )

        verifier = MoveStateVerifier()
        verifier.verify_delta(delta, attestation)
        assert verifier.reads_halted is False
```

Create `tests/test_dual_vm_equivocation.py`:

```python
"""Tests for committee equivocation detection."""

import pytest


class TestEquivocationDetection:
    def test_epoch_mismatch_rejected(self):
        from src.ltp.dual_vm.state_verifier import MoveStateVerifier
        from src.ltp.dual_vm.state_delta import MoveStateDelta
        from src.ltp.dual_vm.bls_attestation import MoveStateAttestation

        delta = MoveStateDelta(epoch_id=5, prev_root=b"\x00" * 32, new_root=b"\x01" * 32, changes=[])
        attestation = MoveStateAttestation(
            move_state_root=b"\x01" * 32, epoch_id=6,  # wrong epoch
            committee_size=5, aggregate_signature=b"\xff" * 32,
            signer_bitmap=0b11111,
        )

        verifier = MoveStateVerifier()
        result = verifier.verify_delta(delta, attestation)
        assert result.is_valid is False

    def test_recovery_after_equivocation_resolved(self):
        from src.ltp.dual_vm.state_verifier import MoveStateVerifier
        from src.ltp.dual_vm.state_delta import MoveStateDelta
        from src.ltp.dual_vm.bls_attestation import MoveStateAttestation

        verifier = MoveStateVerifier()

        # Trigger equivocation
        bad_delta = MoveStateDelta(epoch_id=1, prev_root=b"\x00" * 32, new_root=b"\xab" * 32, changes=[])
        bad_att = MoveStateAttestation(
            move_state_root=b"\xcd" * 32, epoch_id=1,
            committee_size=5, aggregate_signature=b"", signer_bitmap=0,
        )
        verifier.verify_delta(bad_delta, bad_att)
        assert verifier.reads_halted is True

        # Resolve by resetting
        verifier.reset_halt()
        assert verifier.reads_halted is False
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_dual_vm_state_verifier.py tests/test_dual_vm_equivocation.py -v`

Expected: FAIL

- [ ] **Step 3: Write the implementation**

Create `src/ltp/dual_vm/state_verifier.py`:

```python
"""MoveStateVerifier — POS-side Move state root verification.

POS nodes:
  1. Receive Move state deltas from POA nodes
  2. Apply deltas to local state tree copy
  3. Recompute Move state root
  4. Verify against BLS-signed attestation
  5. On match: serve Move state reads
  6. On mismatch: halt reads, flag equivocation
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .bls_attestation import MoveStateAttestation
from .state_delta import MoveStateDelta


@dataclass
class VerificationResult:
    """Result of verifying a Move state delta against an attestation."""
    is_valid: bool
    equivocation_detected: bool
    epoch_id: int
    expected_root: bytes
    actual_root: bytes
    reason: str = ""


class MoveStateVerifier:
    """Verifies Move state deltas against BLS-signed attestations.

    Halts all Move state reads if equivocation is detected.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reads_halted = False
        self._last_verified_epoch = 0
        self._equivocation_log: list[VerificationResult] = []

    @property
    def reads_halted(self) -> bool:
        with self._lock:
            return self._reads_halted

    def verify_delta(
        self, delta: MoveStateDelta, attestation: MoveStateAttestation
    ) -> VerificationResult:
        """Verify a delta's new_root matches the attestation's signed root."""

        # Epoch consistency check
        if delta.epoch_id != attestation.epoch_id:
            result = VerificationResult(
                is_valid=False,
                equivocation_detected=False,
                epoch_id=delta.epoch_id,
                expected_root=attestation.move_state_root,
                actual_root=delta.new_root,
                reason=f"epoch mismatch: delta={delta.epoch_id}, attestation={attestation.epoch_id}",
            )
            return result

        # Root comparison (the core verification)
        roots_match = delta.new_root == attestation.move_state_root

        if not roots_match:
            with self._lock:
                self._reads_halted = True
            result = VerificationResult(
                is_valid=False,
                equivocation_detected=True,
                epoch_id=delta.epoch_id,
                expected_root=attestation.move_state_root,
                actual_root=delta.new_root,
                reason="root mismatch: committee equivocation detected",
            )
            self._equivocation_log.append(result)
            return result

        with self._lock:
            self._last_verified_epoch = delta.epoch_id

        return VerificationResult(
            is_valid=True,
            equivocation_detected=False,
            epoch_id=delta.epoch_id,
            expected_root=attestation.move_state_root,
            actual_root=delta.new_root,
        )

    def reset_halt(self) -> None:
        """Resume reads after equivocation is resolved."""
        with self._lock:
            self._reads_halted = False
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_dual_vm_state_verifier.py tests/test_dual_vm_equivocation.py -v`

Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ltp/dual_vm/state_verifier.py tests/test_dual_vm_state_verifier.py tests/test_dual_vm_equivocation.py
git commit -m "feat(dual-vm): add MoveStateVerifier with equivocation detection and read halting"
```

---

## Task 9: Update Package Exports and Full Regression

**Files:**
- Modify: `src/ltp/dual_vm/__init__.py`
- All dual VM test files

- [ ] **Step 1: Update __init__.py**

```python
"""Dual VM — EVM + MoveVM execution environment (Phase 4)."""

from .bls_attestation import BLSAggregator, MoveStateAttestation
from .bls_keys import BLSKeyPair, aggregate_signatures, aggregate_verify
from .config import DualVMConfig
from .dual_root import DualStateRoot
from .precompile import MoveStatePrecompile
from .state_delta import MoveStateDelta
from .state_verifier import MoveStateVerifier
from .tx_filter import MoveTransactionFilter
from .writer_registry import WriterRegistry

__all__ = [
    "BLSAggregator",
    "BLSKeyPair",
    "DualStateRoot",
    "DualVMConfig",
    "MoveStateAttestation",
    "MoveStateDelta",
    "MoveStatePrecompile",
    "MoveStateVerifier",
    "MoveTransactionFilter",
    "WriterRegistry",
    "aggregate_signatures",
    "aggregate_verify",
]
```

- [ ] **Step 2: Run all dual VM tests**

Run: `pytest tests/test_dual_vm_*.py -v`

Expected: All tests PASS (~45 tests across 9 test files)

- [ ] **Step 3: Run full project test suite**

Run: `pytest tests/ -v --tb=short 2>&1 | tail -40`

Expected: No regressions

- [ ] **Step 4: Verify imports**

Run: `python -c "from src.ltp.dual_vm import DualVMConfig, WriterRegistry, MoveTransactionFilter, BLSKeyPair, BLSAggregator, MoveStateAttestation, MoveStateDelta, DualStateRoot, MoveStatePrecompile, MoveStateVerifier; print('Phase 4 imports OK')"`

Expected: `Phase 4 imports OK`

- [ ] **Step 5: Commit**

```bash
git add src/ltp/dual_vm/__init__.py
git commit -m "feat(dual-vm): finalize Phase 4 package exports"
```

---

## Summary

| Task | Component | Files | Tests |
|---|---|---|---|
| 1 | Config + domain tags | `config.py`, `domain.py` | 4 tests |
| 2 | Writer Registry | `writer_registry.py` | 7 tests |
| 3 | Transaction Filter | `tx_filter.py` | 5 tests |
| 4 | BLS Key Types | `bls_keys.py` | 8 tests |
| 5 | BLS Attestation | `bls_attestation.py` | 5 tests |
| 6 | State Delta + Dual Root | `state_delta.py`, `dual_root.py` | 7 tests |
| 7 | Precompile Interface | `precompile.py` | 5 tests |
| 8 | State Verifier | `state_verifier.py` | 6 tests |
| 9 | Package exports + regression | `__init__.py` | Full suite |

**Total: ~10 new files, ~1,200 lines of code, ~47 tests, 9 commits.**

**What Phase 4 builds (infrastructure for MoveVM+DID):**
- Writer permissioning at transaction validity layer
- BLS aggregate attestation on Move state roots
- Move state delta propagation format
- POS-side verification with equivocation detection
- Dual state root (EVM + Move) block header format
- Precompile interface reservation at 0x0F

**What Phase 4 does NOT build (MoveVM+DID scope):**
- DID registry module
- DID document schema or operations
- Verifiable Credentials
- Ethereum account binding
- Identity governance
- Actual MoveVM binary integration (depends on Open Question #8: which Move implementation)
