# ETP Development Makefile
# ========================
# Quick commands for testing, building, and deployment verification.

.PHONY: test test-python test-contracts test-integration test-all
.PHONY: build lint clean ci-integration

# ── Python Tests ────────────────────────────────────────────────────────

test-python:
	pytest tests/ -v --ignore=tests/test_contract_integration.py

test-python-fast:
	pytest tests/ -x -q --ignore=tests/test_contract_integration.py

# ── Solidity Tests ──────────────────────────────────────────────────────

test-contracts:
	cd contracts && forge test -vvv

# ── Contract Integration (requires anvil) ───────────────────────────────

ci-integration: _anvil-start _deploy-local test-integration _anvil-stop

_anvil-start:
	@echo "Starting anvil..."
	@anvil --silent &
	@sleep 2

_deploy-local:
	@echo "Building and deploying contracts to local anvil..."
	@cd contracts && forge install foundry-rs/forge-std 2>/dev/null || true
	@cd contracts && forge build
	@cd contracts && forge script script/Deploy.s.sol \
		--rpc-url http://localhost:8545 --broadcast 2>/dev/null

test-integration:
	pytest tests/test_contract_integration.py -v

_anvil-stop:
	@echo "Stopping anvil..."
	@pkill -f anvil 2>/dev/null || true

# ── Full Suite ──────────────────────────────────────────────────────────

test-all: test-python test-contracts ci-integration
	@echo "All tests passed."

test: test-python

# ── Build & Lint ────────────────────────────────────────────────────────

build:
	pip install -e ".[dev]"

lint:
	python -m py_compile src/ltp/__init__.py
	@echo "Syntax OK"

# ── Clean ───────────────────────────────────────────────────────────────

clean:
	rm -rf __pycache__ .pytest_cache htmlcov .coverage
	rm -rf src/ltp/__pycache__ tests/__pycache__
	rm -rf contracts/out contracts/cache
	@echo "Clean."
