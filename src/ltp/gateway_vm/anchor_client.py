"""DevnetAnchorClient — gateway-specific submission to LTPAnchorRegistry.

Converts GatewayAttestation → AnchorSubmission and submits via an
injectable submit_fn. In production the submit_fn wraps AnchorClient.anchor();
in tests it can be a mock.

The from_gateway_config() factory creates the real AnchorClient connection
when RPC is available.
"""

from __future__ import annotations

import hashlib
import time
from typing import Callable

from ..anchor.submission import AnchorSubmission
from .config import GatewayVMConfig
from .writer import GatewayAttestation

# Receipt type string registered for gateway attestations.
_RECEIPT_TYPE = "GATEWAY_ATTEST"


class DevnetAnchorClient:
    """Submits gateway attestations to the GSX devnet LTPAnchorRegistry.

    Uses composition over inheritance — inject submit_fn for testability,
    use from_gateway_config() for real RPC connections.
    """

    def __init__(self, submit_fn: Callable[[AnchorSubmission], str]) -> None:
        self._submit_fn = submit_fn

    @classmethod
    def from_gateway_config(
        cls,
        config: GatewayVMConfig,
        operator_private_key: str,
    ) -> DevnetAnchorClient:
        """Create from gateway config with a real AnchorClient backend."""
        if not config.dest_rpc_url:
            raise ValueError("dest_rpc_url is required for DevnetAnchorClient")
        if not config.dest_registry_address:
            raise ValueError("dest_registry_address is required for DevnetAnchorClient")

        from ..anchor.client import AnchorClient

        client = AnchorClient(
            rpc_url=config.dest_rpc_url,
            contract_address=config.dest_registry_address,
            private_key=operator_private_key,
            chain_id=config.dest_chain_id,
        )
        return cls(submit_fn=client.anchor)

    def submit_attestation(self, attestation: GatewayAttestation) -> str:
        """Convert attestation to AnchorSubmission and submit. Returns tx hash."""
        submission = _attestation_to_submission(attestation)
        return self._submit_fn(submission)

    def as_anchor_fn(self) -> Callable[[GatewayAttestation], str]:
        """Return a callable compatible with GatewayVMService.anchor_fn."""
        return self.submit_attestation


def _attestation_to_submission(attestation: GatewayAttestation) -> AnchorSubmission:
    """Map gateway attestation fields to AnchorSubmission fields."""
    digest = attestation.digest
    # Ensure exactly 32 bytes — truncate or pad
    anchor_digest = digest[:32] if len(digest) >= 32 else digest.ljust(32, b"\x00")

    # Use event_id hash as entity_id_hash (deterministic 32B identifier)
    entity_id_hash = hashlib.sha3_256(attestation.event_id.encode()).digest()

    # Merkle root: hash of the signed event bytes (single-leaf "tree")
    merkle_root = hashlib.sha3_256(attestation.event_bytes).digest()

    # Policy hash: hash of the receipt type (gateway attestation policy)
    policy_hash = hashlib.sha3_256(_RECEIPT_TYPE.encode()).digest()

    return AnchorSubmission(
        anchor_digest=anchor_digest,
        entity_id_hash=entity_id_hash,
        merkle_root=merkle_root,
        policy_hash=policy_hash,
        signer_vk_hash=attestation.signer_vk_fingerprint,
        sequence=0,
        valid_until=int(time.time()) + 86400,
        target_chain_id=attestation.dest_chain_id,
        receipt_type=_RECEIPT_TYPE,
    )
