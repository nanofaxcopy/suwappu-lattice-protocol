"""Tests for Gateway VM config validation and boot logic."""

import pytest

from src.ltp.gateway_vm.boot import validate_config
from src.ltp.gateway_vm.config import GatewayVMConfig


class TestValidateConfig:
    def test_all_defaults_missing_four_fields(self):
        config = GatewayVMConfig()
        missing = validate_config(config)
        assert "ETP_GATEWAY_VM_SOURCE_RPC_URL" in missing
        assert "ETP_GATEWAY_VM_SOURCE_BRIDGE_CONTRACT" in missing
        assert "ETP_GATEWAY_VM_DEST_RPC_URL" in missing
        assert "ETP_GATEWAY_VM_DEST_REGISTRY" in missing
        assert len(missing) == 4

    def test_partial_missing(self):
        config = GatewayVMConfig(
            source_rpc_url="http://source:8545",
            source_bridge_contract="0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
            dest_rpc_url="",
            dest_registry_address="",
        )
        missing = validate_config(config)
        assert "ETP_GATEWAY_VM_DEST_RPC_URL" in missing
        assert "ETP_GATEWAY_VM_DEST_REGISTRY" in missing
        assert len(missing) == 2

    def test_all_present_returns_empty(self):
        config = GatewayVMConfig(
            source_rpc_url="http://source:8545",
            source_bridge_contract="0x5083194d9e8EB54Fc397E69A518Be9503C767Dd0",
            dest_rpc_url="http://dest:8545",
            dest_registry_address="0xB29d8BFF4973D1D7bcB10E32112EBB8fdd530bF4",
        )
        missing = validate_config(config)
        assert missing == []

    def test_whitespace_only_counts_as_missing(self):
        config = GatewayVMConfig(
            source_rpc_url="   ",
            source_bridge_contract="0x5083",
            dest_rpc_url="http://dest:8545",
            dest_registry_address="0xreg",
        )
        missing = validate_config(config)
        assert "ETP_GATEWAY_VM_SOURCE_RPC_URL" in missing
