---
title: ML-DSA Signature Verifiers
description: An ERC-7913 verifier profile for FIPS-204 ML-DSA keys, with fixed key encoding, mandatory context separation, and fail-closed precompile delegation
author: TBD
discussions-to: TBD
status: Draft
type: Standards Track
category: ERC
requires: 7913
---

<!-- EIP format requires both a frontmatter `title` and a matching H1. -->
<!-- markdownlint-disable-next-line MD025 -->
# ERC-XXXX: ML-DSA Signature Verifiers

> **Repo note, not part of the proposal.** Working draft held in-tree; not
> submitted to `ethereum/ERCs`. Rationale for pursuing this at all — and why
> it is deliberately the *third* priority behind amending
> [EIP-8051](https://eips.ethereum.org/EIPS/eip-8051) — is in
> [`../design-decisions/PQ_ONCHAIN_VERIFICATION.md`](../design-decisions/PQ_ONCHAIN_VERIFICATION.md).
> The `author` and `discussions-to` fields must be filled before submission.

## Abstract

This ERC profiles [ERC-7913](https://eips.ethereum.org/EIPS/eip-7913) for
NIST FIPS-204 ML-DSA keys. It fixes the `key` encoding to the FIPS-204
public key, infers the parameter set from key length, mandates a non-empty
FIPS-204 context string, and specifies how a verifier delegates to an
ML-DSA verification precompile such that a chain lacking that precompile
fails closed.

It does not define a precompile and does not make ML-DSA verification
cheap. It defines the interface that stays constant across chains that
have one, chains that do not yet, and chains that later gain one.

## Motivation

ERC-7913 lets an account be controlled by a key that has no Ethereum
address, via a stateless verifier contract addressed as `verifier ‖ key`.
It is the natural integration point for post-quantum signers: an
ERC-1271 or ERC-4337 account, a multisig, or a bridge registry can hold an
ML-DSA signer without any of them learning what ML-DSA is.

What ERC-7913 deliberately leaves open is *how a given key type is
encoded*. For ML-DSA that question has at least four defensible answers
(FIPS-204 encoded public key; NTT-expanded public key; a hash committing to
either; a registry index), and they are mutually incompatible: a signer
blob written for one verifier silently means something else to another. If
each deployment picks its own, the "deploy one verifier, reuse it
everywhere" property that motivates ERC-7913 is lost for exactly the key
type that most needs it, since PQ keys are too large to re-register
casually.

Two further gaps are specific to ML-DSA and are the substance of this
proposal:

1. **Domain separation.** FIPS-204 defines `ML-DSA.Sign(sk, M, ctx)`. A
   verifier that omits `ctx` verifies bare digests, so one signature is
   valid for every protocol presenting the same 32 bytes. ERC-7913's
   `hash` parameter is caller-supplied and carries no guarantee of
   domain separation on its own.
2. **Fail-closed delegation.** A `staticcall` to a precompile address on a
   chain that has not deployed it returns success with empty return data,
   which naive verifier code reads as a passing verification. Every
   deployment of a delegating verifier faces this; it should be specified
   once, not rediscovered per implementation.

## Specification

The key words "MUST", "MUST NOT", "SHOULD", and "MAY" are to be
interpreted as described in RFC 2119 and RFC 8174.

### Interface

Conforming verifiers implement `IERC7913SignatureVerifier` unchanged:

```solidity
interface IERC7913SignatureVerifier {
    function verify(bytes calldata key, bytes32 hash, bytes calldata signature)
        external
        view
        returns (bytes4);
}
```

Returning `0x024ad318` indicates a valid signature. A conforming verifier
MUST return `0xffffffff` or revert in every other case, including when the
underlying verification mechanism is unavailable.

### Key encoding

`key` MUST be the FIPS-204 encoded ML-DSA public key (`ρ ‖ t₁`), exactly
as produced by `ML-DSA.KeyGen` and consumed by `ML-DSA.Verify`. No
prefix, no length field, no expansion.

The parameter set is determined by `key.length`:

| `key.length` | Parameter set | NIST level | Required `signature.length` |
|---:|---|---:|---:|
| 1312 | ML-DSA-44 | 2 | 2420 |
| 1952 | ML-DSA-65 | 3 | 3309 |
| 2592 | ML-DSA-87 | 5 | 4627 |

Any other `key.length` MUST be rejected. A `signature.length` that does
not match the row selected by `key.length` MUST be rejected. A verifier
MAY support a subset of rows; unsupported rows MUST be rejected, not
silently accepted.

Encoded public keys are unambiguous across the three parameter sets, so
no discriminator byte is needed. Omitting one keeps `verifier ‖ key`
byte-identical to the key material an application already holds.

### Verification

`verify(key, hash, signature)` MUST return the magic value if and only if:

```
ML-DSA.Verify(pk = key, M = hash, ctx = CONTEXT, signature) == true
```

where `hash` is treated as a 32-byte message, and `CONTEXT` is the ASCII
byte string:

```
ERC-XXXX/ML-DSA/1
```

(17 bytes, no terminator, no length prefix beyond FIPS-204's own
encoding of `ctx`.)

`CONTEXT` MUST NOT be empty, MUST NOT be caller-supplied, and MUST be
immutable in the verifier's code. Signers producing signatures for a
conforming verifier MUST pass the identical `ctx` to `ML-DSA.Sign`.

Applications needing their own domain separation MUST obtain it inside
`hash` — via [ERC-712](https://eips.ethereum.org/EIPS/eip-712),
[ERC-7739](https://eips.ethereum.org/EIPS/eip-7739), or an equivalent —
and MUST NOT vary `CONTEXT` to achieve it. `CONTEXT` separates
*this ERC's verification surface* from every other use of the same key;
`hash` separates one application from another.

### Precompile delegation

A verifier that delegates to an ML-DSA verification precompile MUST treat
absence as failure. Specifically, given a `staticcall` to the precompile:

1. If the call reverts, verification fails.
2. **If the call succeeds with empty return data, verification fails.**
   This is the case on a chain where the precompile is not deployed: the
   target address holds no code and the call trivially succeeds.
3. If the call succeeds with non-empty return data, verification succeeds
   if and only if that data decodes to the precompile's defined
   success value.

A verifier MUST NOT infer success from the absence of a revert.

Verifiers SHOULD expose a view function reporting whether the delegated
mechanism is available on the current chain, so that integrators can
detect a misconfigured deployment without submitting a signature to it.

### Statelessness

Per ERC-7913, conforming verifiers MUST be stateless, MUST NOT be
upgradeable, and MUST NOT read storage during `verify`. Precompile
addresses and `CONTEXT` MUST be compile-time constants or immutables set
at construction.

## Rationale

### Why the encoded key rather than the expanded key

An ERC-7913 signer is `verifier ‖ key`, so `key` is carried by every
system that stores the signer. The FIPS-204 encoded key is 1312/1952/2592
bytes; the NTT-expanded form is 20512 bytes at ML-DSA-44 and 36896 at
ML-DSA-65. Since [EIP-7623](https://eips.ethereum.org/EIPS/eip-7623),
calldata-heavy transactions pay a floor of 40 gas per non-zero byte, and
PQ key material is effectively all non-zero — so the expanded form costs
roughly 820k gas per ML-DSA-44 verification in calldata alone, against
roughly 53k for the encoded form.

The expanded form exists to let a verifier skip NTT expansion. That is a
real saving in compute, and a losing trade against a calldata term two
orders of magnitude larger.

**Dependency worth stating plainly:** this choice assumes an underlying
precompile that accepts FIPS-204 encoded keys. EIP-8051 as currently
drafted accepts only the expanded form, which no contract can produce
from an encoded key on-chain at feasible cost. If that does not change,
a conforming verifier is not implementable over EIP-8051, and this ERC
would need a companion profile keying on the expanded form — inheriting
its costs. Amending EIP-8051 is the better outcome and the higher
priority.

### Why a mandatory, fixed context string

FIPS-204 provides `ctx` precisely for this, so using it costs nothing and
requires no new construction. Fixing it rather than exposing it keeps
`verify`'s signature identical to ERC-7913's and keeps the verifier
stateless, at the price of one indirection for applications: domain
separation goes in `hash`, which is where ERC-712/ERC-7739 already put it.

The alternative — `ctx = ""` — makes any signature over a 32-byte digest
valid for any other protocol presenting those bytes. For a verifier
intended to guard accounts and bridges, that is not an acceptable default.

### Why parameter set by length

The three encoded key lengths are distinct, so a discriminator byte adds
cost and a second source of truth. Length-based dispatch also makes the
signature-length cross-check natural, catching parameter-set confusion
before any verification work.

### Why not define a registry

A 32-byte key handle backed by a registry is cheaper still. It is also
stateful, which ERC-7913 verifiers must not be, and it needs a
canonicalization decision (what the handle commits to) that belongs with
whichever proposal defines the registry. This ERC stays in ERC-7913's
lane; a handle-based profile is compatible future work.

## Backwards Compatibility

Additive. Conforming verifiers are ordinary ERC-7913 verifiers and work
with existing ERC-7913 consumers unmodified. No change to ERC-7913 itself.

Signature corpora produced with `ctx = ""` — which is what most existing
ML-DSA deployments produce, since `ctx` is rarely plumbed through library
APIs — are **not** verifiable under this ERC and must be re-signed. This
is deliberate: accepting them would mean accepting the domain-separation
gap this proposal exists to close.

## Reference Implementation

Illustrative; delegates to a precompile taking `key ‖ ctx ‖ hash ‖
signature`. Exact input encoding is the precompile's to define — this
shows the fail-closed structure, not a settled ABI. Not audited, not
deployed.

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract MLDSAVerifier {
    bytes4 internal constant MAGIC = 0x024ad318;
    bytes4 internal constant FAIL  = 0xffffffff;

    bytes  internal constant CONTEXT = "ERC-XXXX/ML-DSA/1";

    address internal immutable PRECOMPILE;

    constructor(address precompile) {
        PRECOMPILE = precompile;
    }

    function verify(bytes calldata key, bytes32 hash, bytes calldata signature)
        external
        view
        returns (bytes4)
    {
        if (!_lengthsAgree(key.length, signature.length)) return FAIL;

        (bool ok, bytes memory ret) = PRECOMPILE.staticcall(
            abi.encodePacked(key, CONTEXT, hash, signature)
        );

        // Empty returndata means the precompile is absent on this chain:
        // the staticcall to a codeless address succeeded vacuously.
        if (!ok || ret.length == 0) return FAIL;

        return abi.decode(ret, (uint256)) == 1 ? MAGIC : FAIL;
    }

    /// @notice False when the precompile is absent, so a misconfigured
    ///         deployment is detectable without submitting a signature.
    function available() external view returns (bool) {
        (bool ok, bytes memory ret) = PRECOMPILE.staticcall(hex"00");
        return ok ? ret.length != 0 : true;
    }

    function _lengthsAgree(uint256 keyLen, uint256 sigLen)
        internal
        pure
        returns (bool)
    {
        if (keyLen == 1312) return sigLen == 2420; // ML-DSA-44
        if (keyLen == 1952) return sigLen == 3309; // ML-DSA-65
        if (keyLen == 2592) return sigLen == 4627; // ML-DSA-87
        return false;
    }
}
```

## Security Considerations

**Absent precompile.** The failure mode this proposal most wants to
prevent: a `staticcall` to an undeployed precompile address succeeds with
empty return data, so a verifier that checks only the success flag accepts
every signature, including forgeries. Deploying such a verifier to a chain
without the precompile hands control of every account using it to anyone.
Hence the explicit rule that empty return data is failure, and the
`available()` probe.

**Context reuse.** `CONTEXT` separates this ERC's surface from other uses
of the same ML-DSA key. It does *not* separate applications from each
other — two applications that hash the same content into `hash` produce
interchangeable signatures. Applications MUST domain-separate inside
`hash`.

**Parameter-set downgrade.** Verifiers supporting several parameter sets
let a signer be swapped for a weaker one if the consuming account does not
pin the expected `key`. ERC-7913 consumers already store the full signer
blob, so the key is pinned by construction — but consumers that store only
a hash of the signer, or that allow signer rotation, MUST enforce the
expected parameter set themselves. Deployments with a fixed security floor
SHOULD deploy a single-parameter-set verifier.

**Key sizes and gas griefing.** ML-DSA public keys and signatures are
kilobytes. Contracts accepting caller-supplied signers should bound the
number verified per transaction; a 5-of-n threshold check at ML-DSA-65 is
several hundred thousand gas of calldata before any verification work.

**Not a substitute for a fraud proof.** A verifier answers "did this key
sign this digest". It does not establish that the digest means what a
consuming protocol assumes, that the signer is authorized, or that the
signature is fresh. Replay protection and authorization remain the
consumer's responsibility.

## Copyright

Copyright and related rights waived via
[CC0](https://creativecommons.org/publicdomain/zero/1.0/).
