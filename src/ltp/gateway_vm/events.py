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
