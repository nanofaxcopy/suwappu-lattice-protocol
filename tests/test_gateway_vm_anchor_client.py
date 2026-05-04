"""Tests for DevnetAnchorClient — gateway-specific AnchorClient extension."""

import pytest
from unittest.mock import MagicMock, patch
from src.ltp.keypair import KeyPair


@pytest.fixture(scope="module")
def gateway_kp():
    return KeyPair.generate("anchor-client-test")


def _make_attestation(gateway_kp, tx_hash="0xabc123"):
    from src.ltp.gateway_vm.events import BridgeEvent
    from src.ltp.gateway_vm.writer import AttestationWriter

    event = BridgeEvent(
        source_chain_id=84532,
        bridge_contract="0x5083",
        tx_hash=tx_hash,
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
    def test_create_with_injected_submit(self):
        from src.ltp.gateway_vm.anchor_client import DevnetAnchorClient

        mock_submit = MagicMock(return_value="0xtxhash")
        client = DevnetAnchorClient(submit_fn=mock_submit)
        assert client is not None

    def test_from_gateway_config_requires_rpc_url(self):
        from src.ltp.gateway_vm.anchor_client import DevnetAnchorClient
        from src.ltp.gateway_vm.config import GatewayVMConfig

        config = GatewayVMConfig(dest_rpc_url="")
        with pytest.raises(ValueError, match="dest_rpc_url"):
            DevnetAnchorClient.from_gateway_config(
                config=config,
                operator_private_key="0xdeadbeef",
            )

    def test_from_gateway_config_requires_registry_address(self):
        from src.ltp.gateway_vm.anchor_client import DevnetAnchorClient
        from src.ltp.gateway_vm.config import GatewayVMConfig

        config = GatewayVMConfig(
            dest_rpc_url="https://rpc.example.com",
            dest_registry_address="",
        )
        with pytest.raises(ValueError, match="dest_registry_address"):
            DevnetAnchorClient.from_gateway_config(
                config=config,
                operator_private_key="0xdeadbeef",
            )


class TestSubmitAttestation:
    def test_submit_calls_submit_fn(self, gateway_kp):
        from src.ltp.gateway_vm.anchor_client import DevnetAnchorClient

        mock_submit = MagicMock(return_value="0xtxhash123")
        client = DevnetAnchorClient(submit_fn=mock_submit)

        attestation = _make_attestation(gateway_kp)
        tx_hash = client.submit_attestation(attestation)

        assert tx_hash == "0xtxhash123"
        mock_submit.assert_called_once()

    def test_submit_raises_on_failure(self, gateway_kp):
        from src.ltp.gateway_vm.anchor_client import DevnetAnchorClient

        client = DevnetAnchorClient(
            submit_fn=MagicMock(side_effect=RuntimeError("Circuit breaker OPEN"))
        )

        attestation = _make_attestation(gateway_kp)
        with pytest.raises(RuntimeError, match="Circuit breaker"):
            client.submit_attestation(attestation)


class TestAnchorFnAdapter:
    def test_as_anchor_fn_returns_callable(self, gateway_kp):
        from src.ltp.gateway_vm.anchor_client import DevnetAnchorClient

        client = DevnetAnchorClient(submit_fn=MagicMock(return_value="0xtx"))
        fn = client.as_anchor_fn()
        assert callable(fn)

        attestation = _make_attestation(gateway_kp)
        result = fn(attestation)
        assert result == "0xtx"


class TestAttestationToSubmission:
    def test_converts_attestation_fields(self, gateway_kp):
        from src.ltp.gateway_vm.anchor_client import DevnetAnchorClient

        captured = {}

        def capture_submit(submission):
            captured["submission"] = submission
            return "0xtx"

        client = DevnetAnchorClient(submit_fn=capture_submit)
        attestation = _make_attestation(gateway_kp)
        client.submit_attestation(attestation)

        sub = captured["submission"]
        # anchor_digest is the first 32 bytes of attestation digest
        assert len(sub.anchor_digest) == 32
        assert sub.signer_vk_hash == attestation.signer_vk_fingerprint
        assert sub.target_chain_id == attestation.dest_chain_id
        assert sub.receipt_type == "GATEWAY_ATTEST"
