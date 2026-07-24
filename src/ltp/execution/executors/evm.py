"""EVMExecutor — wraps EVM interaction as an ExecutionModel.

Follows the same backend-injection pattern as MoveExecutor
(executors/move.py): the executor itself is backend-agnostic, and the
backend supplies the actual execution semantics.

- InMemoryEVMBackend: deterministic hash-chained state root, no real EVM
  semantics. Used when no RPC is configured (tests, offline dev).
- JsonRpcEVMBackend: real backend — submits raw signed transactions to an
  EVM JSON-RPC node (Geth, Anvil, Base Sepolia, ...) via web3.py, reads
  back the receipt and the live state root from the chain tip.

`EVMExecutor()` with no arguments preserves the prior stub's exact
in-memory behavior for backward compatibility with existing callers/tests.
"""

from __future__ import annotations

from typing import Optional, Protocol

from ...primitives import canonical_hash_bytes
from ..model import VM_TAG_EVM
from ..types import StateQuery, StateResult, TxResult


class EVMBackend(Protocol):
    """Abstract backend for EVM execution — in-memory or real JSON-RPC."""

    def execute_transaction(self, tx_bytes: bytes) -> tuple[bool, int, bytes]: ...
    def get_state_root(self) -> bytes: ...
    def query_state(self, query: StateQuery) -> Optional[bytes]: ...


class InMemoryEVMBackend:
    """Deterministic hash-chained state root. No real EVM semantics.

    Preserves the exact behavior of the original stub implementation.
    """

    def __init__(self) -> None:
        self._state_root = canonical_hash_bytes(b"evm-genesis-state")
        self._tx_count = 0

    def execute_transaction(self, tx_bytes: bytes) -> tuple[bool, int, bytes]:
        self._tx_count += 1
        self._state_root = canonical_hash_bytes(
            self._state_root + tx_bytes + self._tx_count.to_bytes(8, "big")
        )
        return True, 21000, b""

    def get_state_root(self) -> bytes:
        return self._state_root

    def query_state(self, query: StateQuery) -> Optional[bytes]:
        return None


class JsonRpcEVMBackend:
    """Real EVM backend — talks to a live JSON-RPC node via web3.py.

    ``tx_bytes`` is expected to be a raw signed transaction (RLP-encoded,
    e.g. produced by ``Account.sign_transaction(...).rawTransaction``).
    Submits it with ``eth_sendRawTransaction``, waits for the receipt, and
    reports success/gas from the receipt status. The state root is read
    live from the chain tip on every call (EVM has no single canonical
    "the" state root API pre-Verkle, so this uses the block header's
    ``stateRoot`` field, matching what a light client would trust).
    """

    def __init__(self, rpc_url: str, timeout: float = 120.0) -> None:
        try:
            from web3 import Web3
        except ImportError as exc:  # pragma: no cover - exercised via skip in tests
            raise ImportError(
                "JsonRpcEVMBackend requires web3.py: pip install -e '.[chain]'"
            ) from exc
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._timeout = timeout

    def execute_transaction(self, tx_bytes: bytes) -> tuple[bool, int, bytes]:
        tx_hash = self._w3.eth.send_raw_transaction(tx_bytes)
        receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=self._timeout)
        success = receipt.status == 1
        return success, receipt.gasUsed, bytes(tx_hash)

    def get_state_root(self) -> bytes:
        block = self._w3.eth.get_block("latest")
        return bytes(block["stateRoot"])

    def query_state(self, query: StateQuery) -> Optional[bytes]:
        # key layout: 20-byte address, optionally followed by a 32-byte
        # storage slot for query_type == "storage".
        if len(query.key) < 20:
            return None
        address = self._w3.to_checksum_address(query.key[:20])

        if query.query_type == "storage":
            if len(query.key) < 52:
                return None
            slot = int.from_bytes(query.key[20:52], "big")
            return bytes(self._w3.eth.get_storage_at(address, slot))
        if query.query_type == "account":
            balance = self._w3.eth.get_balance(address)
            return balance.to_bytes(32, "big")
        if query.query_type == "contract":
            return bytes(self._w3.eth.get_code(address))
        return None


class EVMExecutor:
    """EVM executor — account-based VM (tag 0x01).

    Delegates execution to an EVMBackend. Defaults to InMemoryEVMBackend
    for backward compatibility with zero-argument construction; pass a
    JsonRpcEVMBackend for real on-chain execution.
    """

    vm_tag = VM_TAG_EVM
    vm_name = "evm"
    family = "account"

    def __init__(self, backend: Optional[EVMBackend] = None) -> None:
        self._backend = backend or InMemoryEVMBackend()

    def execute(self, tx_bytes: bytes) -> TxResult:
        """Execute an EVM transaction via the backend."""
        try:
            success, gas_used, return_data = self._backend.execute_transaction(tx_bytes)
        except Exception as exc:
            return TxResult.failed(f"evm execution error: {exc}")
        if success:
            return TxResult.accepted(gas_used=gas_used, return_data=return_data)
        return TxResult.failed("evm execution rejected", gas_used=gas_used)

    def state_root(self) -> bytes:
        """Return current 32-byte EVM state root from the backend."""
        return self._backend.get_state_root()

    def validate_tx(self, tx_bytes: bytes) -> bool:
        return len(tx_bytes) > 0

    def query_state(self, query: StateQuery) -> StateResult:
        """Query EVM account/storage/contract state via the backend."""
        try:
            value = self._backend.query_state(query)
        except Exception:
            return StateResult.not_found()
        if value is not None:
            return StateResult(data=value, found=True)
        return StateResult.not_found()
