"""Tests for FinalityWatcher — confirmation depth tracking."""

import pytest


class TestFinalityWatcher:
    def test_event_below_finality_depth_not_final(self):
        from src.ltp.gateway_vm.finality import FinalityWatcher

        watcher = FinalityWatcher(
            finality_depth=12,
            get_block_number=lambda: 110,
        )
        # 110 - 100 = 10 < 12
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
