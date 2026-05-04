"""Gateway VM boot utilities — config validation and production wiring."""

from __future__ import annotations

from .config import GatewayVMConfig


def validate_config(config: GatewayVMConfig) -> list[str]:
    """Return names of missing required env vars.

    Returns an empty list if all required fields are populated.
    """
    checks = [
        (config.source_rpc_url, "ETP_GATEWAY_VM_SOURCE_RPC_URL"),
        (config.source_bridge_contract, "ETP_GATEWAY_VM_SOURCE_BRIDGE_CONTRACT"),
        (config.dest_rpc_url, "ETP_GATEWAY_VM_DEST_RPC_URL"),
        (config.dest_registry_address, "ETP_GATEWAY_VM_DEST_REGISTRY"),
    ]
    return [name for value, name in checks if not value.strip()]
