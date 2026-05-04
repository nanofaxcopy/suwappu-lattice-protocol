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
