# Standards Work

Ethereum standards artifacts produced by this project: draft proposals we
intend to submit, and prepared feedback on external proposals we depend on.

Nothing in this directory has been submitted or posted. Each file states
its own status and what has to happen before it leaves the repo.

| File | What it is | Status |
|---|---|---|
| [`eip-8051-ltp-feedback.md`](eip-8051-ltp-feedback.md) | Implementer review comment on [EIP-8051](https://eips.ethereum.org/EIPS/eip-8051) (ML-DSA verification precompiles), asking for compact FIPS-204 keys, a `ctx` parameter, and ML-DSA-65/87 support | Drafted, **not posted** — needs sign-off |
| [`erc-draft-mldsa-verifier.md`](erc-draft-mldsa-verifier.md) | Draft ERC: an [ERC-7913](https://eips.ethereum.org/EIPS/eip-7913) verifier profile for ML-DSA keys | Working draft, **not submitted** |

Why these two and in this order:
[`../design-decisions/PQ_ONCHAIN_VERIFICATION.md`](../design-decisions/PQ_ONCHAIN_VERIFICATION.md).

## Conventions

- Draft proposals keep EIP-format frontmatter so they can be submitted
  without reformatting. `author` and `discussions-to` stay `TBD` until
  someone owns them.
- Prepared external comments carry a "Before posting" checklist. Posting
  under the project's name is an external act — treat it as one.
- Gas figures cite their cost model (pre/post [EIP-7623](https://eips.ethereum.org/EIPS/eip-7623))
  and say whether they are arithmetic or measurement. So far they are all
  arithmetic.
