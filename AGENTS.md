# AGENTS.md

Tool-agnostic entry point for AI coding agents working in this repository.
This file is deliberately short: it tells you where the real guidance lives
and which rules are non-negotiable.

## Read these, in order

1. [`CLAUDE.md`](CLAUDE.md) — the in-IDE quick reference: repo layout,
   verify commands, hard rules, gotchas. Applies to every agent, not just
   Claude.
2. [`docs/AI_AGENTS.md`](docs/AI_AGENTS.md) — the full agent guide:
   always-read files, per-tool notes, regression reporting.
3. [`CONTRIBUTING.md`](CONTRIBUTING.md) — human contributor flow; agents
   follow the same PR checklist.

## Non-negotiable rules (summary)

These are enforced by review and prior audit findings — the full list with
rationale is in [`CLAUDE.md`](CLAUDE.md):

- No `Co-Authored-By` footers in commits.
- No `git rebase` on shared branches; merge instead.
- No `--no-verify` or hook bypasses.
- SHA-pin every new GitHub Action (audit finding LTP-A-025).
- `make contracts-secaudit` must be green before proposing changes under
  `contracts/`.
- Never change a deployed-contract address in
  [`docs/DEPLOYED_CONTRACTS.md`](docs/DEPLOYED_CONTRACTS.md) without an
  upgrade plan under `docs/plans/`.
- Do not touch the license field in `pyproject.toml` (tracked in GLO-785).

## How to verify a change

```bash
scripts/verify.sh          # all lanes (lint, semgrep, python, docs, contracts)
scripts/verify.sh fast     # quick fail-fast loop while iterating
```

CI runs the same underlying Makefile targets, so a green `verify.sh` is a
strong predictor of green CI.
