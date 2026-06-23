"""Golden-vector parity tests for the cross-repo SHA3-256 domain digest.

These fixtures pin the byte layout the Python `corridor` package shares with
`suwappu-dag/crates/suwappu-ltp` (Rust). If a Rust-side change moves a field, the
hashes here must be regenerated and the change recorded in
`docs/design-decisions/SUWAPPU_DAG_DB_INTEGRATION.md`.
"""

from __future__ import annotations

from ltp.corridor import (
    DOMAIN_TAG_ATTEST,
    DOMAIN_TAG_CID,
    DOMAIN_TAG_DID_STARK,
    LTP_ATTESTATION_QUORUM_SIZE,
    LTP_ATTESTATION_QUORUM_THRESHOLD,
    ON_CHAIN_COMMITMENT_BYTES,
    AttestationPayload,
    Cid,
    DidRotationStatement,
    sha3_256_domain,
)

# Reference digests reproduced from the byte layout of
# `suwappu-dag/crates/suwappu-ltp` (see attestation.rs, da.rs, did_stark.rs). The
# values come from the Python sha3_256_domain implementation, which is the
# byte-exact mirror of `suwappu_crypto::hash::sha3_256_domain` —
# `H(len(tag) as u32 BE || tag || data)`.
ATTEST_DIGEST_HEX = "383f3a7fcf1b5e5e37d483145ce8e95b1a46c2d0cf038383a348f7ef60b037d2"
CID_ALPHA_HEX = "368cae7527b27ceb8232352f889e4c3736e45927f8c186039aa27f273c6508bf"
CID_BETA_HEX = "0defe348eaa4d7fbb4366d839cde4b5f6eea5d094e7e4f09118de748161366b8"
DID_STARK_DIGEST_HEX = "b250098d46fa1538248ad6382c8da58352bf3bc4d6b9c9f92b07f628185d2d15"


def test_constants_match_paper() -> None:
    assert ON_CHAIN_COMMITMENT_BYTES == 1_600
    assert LTP_ATTESTATION_QUORUM_THRESHOLD == 7
    assert LTP_ATTESTATION_QUORUM_SIZE == 9


def test_attestation_payload_digest_matches_reference() -> None:
    payload = AttestationPayload(
        source_chain=1,
        target_chain=100,
        source_height=42,
        state_root=bytes([0xAB] * 32),
        timestamp_round=7,
    )
    assert payload.canonical_digest().hex() == ATTEST_DIGEST_HEX


def test_cid_matches_reference() -> None:
    assert Cid.of(b"alpha").value.hex() == CID_ALPHA_HEX
    assert Cid.of(b"beta").value.hex() == CID_BETA_HEX
    assert Cid.of(b"alpha") != Cid.of(b"beta")


def test_did_rotation_statement_digest_matches_reference() -> None:
    statement = DidRotationStatement(
        did=bytes(32),
        old_doc_hash=bytes([1]) * 32,
        new_doc_hash=bytes([2]) * 32,
        source_chain=1,
        target_chain=2,
        source_height=10,
    )
    assert statement.digest().hex() == DID_STARK_DIGEST_HEX


def test_sha3_256_domain_length_prefixes_tag() -> None:
    # The boundary-shift attack a naive concatenation is vulnerable to:
    # H("ab" || "c") != H("a" || "bc") under our scheme, because the tag
    # length is folded into the digest input.
    assert sha3_256_domain(b"ab", b"c") != sha3_256_domain(b"a", b"bc")
