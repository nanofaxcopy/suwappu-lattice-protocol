"""FinalityWatcher — confirmation depth tracking for bridge events.

Extends the AnchorVerifier pattern: two-phase verification (depth check
then finality threshold) with reorg detection (negative depth).
"""

from __future__ import annotations

from typing import Callable


class FinalityWatcher:
    """Tracks source chain confirmation depth for bridge events.

    Mirrors the AnchorVerifier.tick() two-phase model:
      Phase 1: Is the event confirmed? (block exists in chain)
      Phase 2: Has it reached finality depth? (enough blocks on top)

    Reorg detection: if current_block < event_block, the event's block
    was removed from the canonical chain.
    """

    def __init__(
        self,
        finality_depth: int,
        get_block_number: Callable[[], int],
    ) -> None:
        self._finality_depth = finality_depth
        self._get_block_number = get_block_number

    def depth(self, block_number: int) -> int:
        """Current confirmation depth. Negative means reorg."""
        return self._get_block_number() - block_number

    def is_final(self, block_number: int) -> bool:
        """True if event block has reached finality depth."""
        return self.depth(block_number) >= self._finality_depth

    def check(self, block_number: int) -> tuple[bool, int, int]:
        """Full status check. Returns (is_final, current_depth, required_depth)."""
        d = self.depth(block_number)
        return d >= self._finality_depth, d, self._finality_depth
