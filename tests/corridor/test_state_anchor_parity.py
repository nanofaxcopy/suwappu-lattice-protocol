"""Cross-repo parity for the suwappu-db state anchor surface.

Golden vectors derived from the genesis-anchor fixture in
`suwappu-db/crates/suwappudb-bridge/tests/solidity_anchor_parity.rs::fixture_valid_anchor_acceptance`:
chain_id=103115120 (SUWAPPU L1), height=0, state_root=[7;32], parent=GENESIS, key=[1;32].
"""

from __future__ import annotations

import pytest

from ltp.corridor import (
    GENESIS_PARENT,
    AuthScheme,
    StateAnchor,
    compute_mac_keccak256,
    hash_anchor_keccak256,
)

CHAIN_ID = 103_115_120
HEIGHT = 0
STATE_ROOT = bytes([7] * 32)
PARENT = GENESIS_PARENT
KEY = bytes([1] * 32)


# These come from the parity fixture; the Solidity contract computes the
# same bytes via Keccak256 (see `LTPAnchorRegistry.computeMac` /
# `hashAnchor`).
SOLIDITY_MAC_HEX = "b82cd56954458dad3dee397928f1c91674a62b086d8edfd8418f3ef3f5e48ef2"
SOLIDITY_HASH_HEX = "3c0af8631fdec59fb35b07bb493530bc66417573e0528b45b45a89af171826ac"

# BLAKE3 path (Rust-canonical) — only run when blake3 is installed.
BLAKE3_MAC_HEX = "bc273659d47f34a82ec401b382e14f4383d4ba672b8a2eaccded66e3f897772a"
BLAKE3_HASH_HEX = "75a3cbc07c4d817601f8119d7146d7d6a92ef70841a62369bffcb877ad952116"


def _blake3_available() -> bool:
    try:
        import blake3  # noqa: F401

        return True
    except ImportError:
        return False


def test_state_anchor_genesis_fields() -> None:
    mac = compute_mac_keccak256(CHAIN_ID, HEIGHT, STATE_ROOT, PARENT, KEY)
    anchor = StateAnchor(
        chain_id=CHAIN_ID,
        height=HEIGHT,
        state_root=STATE_ROOT,
        parent=PARENT,
        mac=mac,
        auth_scheme=AuthScheme.BLAKE3_MAC,
    )
    assert anchor.height == 0
    assert anchor.parent == bytes(32)
    assert int(anchor.auth_scheme) == 0


def test_solidity_mac_matches_fixture() -> None:
    mac = compute_mac_keccak256(CHAIN_ID, HEIGHT, STATE_ROOT, PARENT, KEY)
    assert mac.hex() == SOLIDITY_MAC_HEX


def test_solidity_hash_matches_fixture() -> None:
    mac = bytes.fromhex(SOLIDITY_MAC_HEX)
    anchor = StateAnchor(
        chain_id=CHAIN_ID,
        height=HEIGHT,
        state_root=STATE_ROOT,
        parent=PARENT,
        mac=mac,
        auth_scheme=AuthScheme.BLAKE3_MAC,
    )
    assert hash_anchor_keccak256(anchor).hex() == SOLIDITY_HASH_HEX


def test_auth_scheme_invisible_to_solidity_hash() -> None:
    # Solidity hash does NOT fold in auth_scheme; the same anchor under
    # different schemes hashes to the same on-chain value.
    mac = bytes.fromhex(SOLIDITY_MAC_HEX)
    a = StateAnchor(
        chain_id=CHAIN_ID,
        height=HEIGHT,
        state_root=STATE_ROOT,
        parent=PARENT,
        mac=mac,
        auth_scheme=AuthScheme.BLAKE3_MAC,
    )
    b = StateAnchor(
        chain_id=CHAIN_ID,
        height=HEIGHT,
        state_root=STATE_ROOT,
        parent=PARENT,
        mac=mac,
        auth_scheme=AuthScheme.ML_DSA_65_HYBRID,
    )
    assert hash_anchor_keccak256(a) == hash_anchor_keccak256(b)


def test_genesis_parent_is_zero() -> None:
    assert GENESIS_PARENT == bytes(32)


@pytest.mark.skipif(not _blake3_available(), reason="blake3 not installed")
def test_blake3_mac_matches_rust_canonical() -> None:
    from ltp.corridor import compute_mac_blake3

    mac = compute_mac_blake3(CHAIN_ID, HEIGHT, STATE_ROOT, PARENT, KEY)
    assert mac.hex() == BLAKE3_MAC_HEX


@pytest.mark.skipif(not _blake3_available(), reason="blake3 not installed")
def test_blake3_hash_folds_in_auth_scheme() -> None:
    from ltp.corridor import compute_mac_blake3, hash_anchor_blake3

    mac = compute_mac_blake3(CHAIN_ID, HEIGHT, STATE_ROOT, PARENT, KEY)
    a = StateAnchor(
        chain_id=CHAIN_ID,
        height=HEIGHT,
        state_root=STATE_ROOT,
        parent=PARENT,
        mac=mac,
        auth_scheme=AuthScheme.BLAKE3_MAC,
    )
    assert hash_anchor_blake3(a).hex() == BLAKE3_HASH_HEX

    # Different auth_scheme → different hash on the Rust path.
    b = StateAnchor(
        chain_id=CHAIN_ID,
        height=HEIGHT,
        state_root=STATE_ROOT,
        parent=PARENT,
        mac=mac,
        auth_scheme=AuthScheme.ML_DSA_65_HYBRID,
    )
    assert hash_anchor_blake3(a) != hash_anchor_blake3(b)
