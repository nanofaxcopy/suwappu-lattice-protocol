#!/usr/bin/env bash
# verify.sh — the single "did I break it?" entrypoint for LTP.
#
# Mirrors the lane pattern used across Suwappu repos (suwappubot
# scripts/verify.sh): each lane delegates to the same Makefile targets
# CI runs, so local verification and CI cannot drift.
#
# Usage:
#   scripts/verify.sh [lane]
#
# Lanes:
#   lint       pre-commit over the whole tree (ruff, ruff-format, solhint, hygiene)
#   semgrep    project semgrep rules in .semgrep/ (crypto lane separation, key handling)
#   python     full Python test suite (make test-python)
#   fast       fail-fast Python suite (make test-python-fast)
#   contracts  Solidity unit tests (make test-contracts; requires foundry)
#   secaudit   full contract security suite (make contracts-secaudit; slow)
#   docs       regenerate the Python API reference (make docs-api; gates docs CI)
#   all        lint + semgrep + python + docs, plus contracts when forge is installed
#
# Exit codes: 0 ok, 1 a lane failed, 2 unknown lane.

set -u

cd "$(dirname "$0")/.."

LANE="${1:-all}"
FAILED=0

run() {
    local name="$1"
    shift
    echo ""
    echo "── verify: ${name} ──────────────────────────────────────────"
    if "$@"; then
        echo "── ${name}: OK"
    else
        echo "── ${name}: FAILED"
        FAILED=1
    fi
}

lane_lint() {
    if ! command -v pre-commit >/dev/null 2>&1; then
        echo "pre-commit not installed (pip install -e '.[dev]'); skipping lint lane" >&2
        return 1
    fi
    pre-commit run --all-files --show-diff-on-failure
}

lane_semgrep() {
    if ! command -v semgrep >/dev/null 2>&1; then
        echo "semgrep not installed (pip install semgrep); skipping semgrep lane" >&2
        return 1
    fi
    # Advisory severity split matches the CI job in lint.yml: ERROR-severity
    # findings are the signal; WARNING-severity findings are the backlog.
    semgrep scan --config .semgrep/ --metrics=off --error src/ scripts/
}

lane_formal() {
    if ! command -v lake >/dev/null 2>&1 && [ ! -x "$HOME/.elan/bin/lake" ]; then
        echo "lean/lake not installed; skipping formal lane" >&2
        echo "  install: curl -sSfL https://elan.lean-lang.org/elan-init.sh | sh -s -- -y" >&2
        return 1
    fi
    formal/lean/verify.sh
}

lane_contracts() {
    if ! command -v forge >/dev/null 2>&1; then
        echo "foundry not installed (https://getfoundry.sh); skipping contracts lane" >&2
        return 1
    fi
    make test-contracts
}

case "$LANE" in
    lint)      run "pre-commit" lane_lint ;;
    semgrep)   run "semgrep" lane_semgrep ;;
    python)    run "python tests" make test-python ;;
    fast)      run "python tests (fast)" make test-python-fast ;;
    formal)    run "lean proofs" lane_formal ;;
    contracts) run "solidity tests" lane_contracts ;;
    secaudit)  run "contract security suite" make contracts-secaudit ;;
    docs)      run "docs-api" make docs-api ;;
    all)
        run "pre-commit" lane_lint
        run "semgrep" lane_semgrep
        run "python tests" make test-python
        run "docs-api" make docs-api
        if command -v lake >/dev/null 2>&1 || [ -x "$HOME/.elan/bin/lake" ]; then
            run "lean proofs" lane_formal
        else
            echo ""
            echo "── lean proofs: SKIPPED (lean not installed)"
        fi
        if command -v forge >/dev/null 2>&1; then
            run "solidity tests" make test-contracts
        else
            echo ""
            echo "── solidity tests: SKIPPED (foundry not installed)"
        fi
        ;;
    *)
        echo "Unknown lane: ${LANE}" >&2
        echo "Lanes: lint semgrep python fast formal contracts secaudit docs all" >&2
        exit 2
        ;;
esac

echo ""
if [ "$FAILED" -ne 0 ]; then
    echo "verify.sh: FAILED (lane: ${LANE})"
    exit 1
fi
echo "verify.sh: OK (lane: ${LANE})"
