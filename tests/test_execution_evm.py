"""Tests for EVMExecutor — in-memory stub and real JSON-RPC backend."""

import pytest

from src.ltp.execution.types import StateQuery, StateResult, TxResult


class TestEVMExecutorDefaults:
    """Zero-argument construction preserves the old stub's exact behavior."""

    def test_satisfies_execution_model(self):
        from src.ltp.execution.executors.evm import EVMExecutor
        from src.ltp.execution.model import VM_TAG_EVM, ExecutionModel

        executor = EVMExecutor()
        assert isinstance(executor, ExecutionModel)
        assert executor.vm_tag == VM_TAG_EVM
        assert executor.vm_name == "evm"
        assert executor.family == "account"

    def test_state_root_returns_32_bytes(self):
        from src.ltp.execution.executors.evm import EVMExecutor

        executor = EVMExecutor()
        root = executor.state_root()
        assert isinstance(root, bytes)
        assert len(root) == 32

    def test_execute_returns_tx_result(self):
        from src.ltp.execution.executors.evm import EVMExecutor

        executor = EVMExecutor()
        result = executor.execute(b"some_tx_data")
        assert isinstance(result, TxResult)
        assert result.success is True

    def test_execute_updates_state_root(self):
        from src.ltp.execution.executors.evm import EVMExecutor

        executor = EVMExecutor()
        root_before = executor.state_root()
        executor.execute(b"some_tx_data")
        root_after = executor.state_root()
        assert root_before != root_after

    def test_query_state(self):
        from src.ltp.execution.executors.evm import EVMExecutor

        executor = EVMExecutor()
        result = executor.query_state(
            StateQuery(target_vm=0x01, query_type="storage", key=b"\x00" * 32)
        )
        assert isinstance(result, StateResult)
        assert result.found is False


class TestEVMExecutorBackendFailure:
    def test_backend_failure_returns_failed_result(self):
        from src.ltp.execution.executors.evm import EVMExecutor

        class FailingBackend:
            def execute_transaction(self, tx_bytes):
                raise ConnectionError("rpc down")

            def get_state_root(self):
                raise ConnectionError("rpc down")

            def query_state(self, query):
                raise ConnectionError("rpc down")

        executor = EVMExecutor(backend=FailingBackend())
        result = executor.execute(b"tx")
        assert result.success is False
        assert "rpc down" in result.error
        assert (
            executor.query_state(
                StateQuery(target_vm=0x01, query_type="account", key=b"\x00" * 20)
            ).found
            is False
        )


class _FakeEth:
    def __init__(self):
        self._receipt_status = 1

    def send_raw_transaction(self, tx_bytes):
        return b"\xaa" * 32

    def wait_for_transaction_receipt(self, tx_hash, timeout=None):
        return _FakeReceipt(self._receipt_status)

    def get_block(self, tag):
        return {"stateRoot": b"\xbb" * 32}

    def get_balance(self, address):
        return 12345

    def get_storage_at(self, address, slot):
        return b"\xcc" * 32

    def get_code(self, address):
        return b"\x60\x00"


class _FakeReceipt:
    def __init__(self, status):
        self.status = status
        self.gasUsed = 21000


class _FakeWeb3:
    def __init__(self):
        self.eth = _FakeEth()

    def to_checksum_address(self, addr):
        return addr


class TestJsonRpcEVMBackend:
    """Exercises JsonRpcEVMBackend against a fake web3.py Eth interface —
    no live network access required.
    """

    def _make_backend(self):
        pytest.importorskip("web3")
        from src.ltp.execution.executors.evm import JsonRpcEVMBackend

        backend = JsonRpcEVMBackend.__new__(JsonRpcEVMBackend)
        backend._timeout = 1.0
        backend._w3 = _FakeWeb3()
        return backend

    def test_execute_transaction_success(self):
        backend = self._make_backend()
        success, gas_used, tx_hash = backend.execute_transaction(b"\x01\x02")
        assert success is True
        assert gas_used == 21000
        assert tx_hash == b"\xaa" * 32

    def test_execute_transaction_reverted(self):
        backend = self._make_backend()
        backend._w3.eth._receipt_status = 0
        success, _gas_used, _tx_hash = backend.execute_transaction(b"\x01\x02")
        assert success is False

    def test_get_state_root(self):
        backend = self._make_backend()
        root = backend.get_state_root()
        assert root == b"\xbb" * 32

    def test_query_state_account_balance(self):
        backend = self._make_backend()
        result = backend.query_state(
            StateQuery(target_vm=0x01, query_type="account", key=b"\x11" * 20)
        )
        assert result == (12345).to_bytes(32, "big")

    def test_query_state_storage(self):
        backend = self._make_backend()
        result = backend.query_state(
            StateQuery(target_vm=0x01, query_type="storage", key=b"\x11" * 20 + b"\x00" * 32)
        )
        assert result == b"\xcc" * 32

    def test_query_state_short_key_returns_none(self):
        backend = self._make_backend()
        result = backend.query_state(
            StateQuery(target_vm=0x01, query_type="account", key=b"\x11" * 5)
        )
        assert result is None
