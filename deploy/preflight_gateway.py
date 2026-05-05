#!/usr/bin/env python3
"""
ETP Gateway VM — Pre-flight Check Script

Validates deployment readiness before starting the Gateway VM container.
Run this after sourcing your .env.gateway file:

    source deploy/.env.gateway && python deploy/preflight_gateway.py

Or pass the env file explicitly:

    set -a; source deploy/.env.gateway; set +a
    python deploy/preflight_gateway.py

Exit codes:
    0 — all checks passed, safe to start container
    1 — one or more checks failed, do not start container

Checks performed:
    1. Required env vars set (5 vars)
    2. Source RPC reachable (eth_blockNumber)
    3. Dest RPC reachable (eth_blockNumber)
    4. Operator key format valid (0x + 64 hex chars)
    5. Bridge contract exists on source chain (eth_getCode)
    6. Registry contract live on dest chain (version() call)
"""

import json
import os
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_VARS = [
    "ETP_GATEWAY_VM_SOURCE_RPC_URL",
    "ETP_GATEWAY_VM_SOURCE_BRIDGE_CONTRACT",
    "ETP_GATEWAY_VM_DEST_RPC_URL",
    "ETP_GATEWAY_VM_DEST_REGISTRY",
    "ETP_GATEWAY_VM_OPERATOR_KEY",
]

# version() function selector: keccak256("version()")[0:4]
VERSION_SELECTOR = "0x54fd4d50"

RPC_TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------------
# JSON-RPC helper
# ---------------------------------------------------------------------------

def _rpc_call(url: str, method: str, params: list) -> dict:
    """
    Execute a raw JSON-RPC 2.0 call over HTTP POST.

    Returns the full JSON response dict on success.
    Raises urllib.error.URLError on network failure.
    Raises ValueError if the response is not valid JSON.
    """
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=RPC_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_required_env_vars() -> tuple[bool, str]:
    """Check that all 5 required environment variables are set and non-empty."""
    missing = [v for v in REQUIRED_VARS if not os.environ.get(v, "").strip()]
    if missing:
        return False, f"missing vars: {', '.join(missing)}"
    return True, "all 5 required vars present"


def check_source_rpc_reachable(url: str) -> tuple[bool, str]:
    """Verify source RPC endpoint responds to eth_blockNumber."""
    try:
        resp = _rpc_call(url, "eth_blockNumber", [])
        block_hex = resp.get("result", "")
        if not isinstance(block_hex, str) or not block_hex.startswith("0x"):
            return False, f"unexpected result: {block_hex!r}"
        block_num = int(block_hex, 16)
        return True, f"block #{block_num}"
    except urllib.error.URLError as exc:
        return False, f"connection error: {exc.reason}"
    except (ValueError, KeyError) as exc:
        return False, f"bad response: {exc}"


def check_dest_rpc_reachable(url: str) -> tuple[bool, str]:
    """Verify destination RPC endpoint responds to eth_blockNumber."""
    try:
        resp = _rpc_call(url, "eth_blockNumber", [])
        block_hex = resp.get("result", "")
        if not isinstance(block_hex, str) or not block_hex.startswith("0x"):
            return False, f"unexpected result: {block_hex!r}"
        block_num = int(block_hex, 16)
        return True, f"block #{block_num}"
    except urllib.error.URLError as exc:
        return False, f"connection error: {exc.reason}"
    except (ValueError, KeyError) as exc:
        return False, f"bad response: {exc}"


def check_operator_key_format(key: str) -> tuple[bool, str]:
    """Validate operator key is 0x-prefixed and exactly 66 chars (0x + 64 hex)."""
    key = key.strip()
    if not key.startswith("0x"):
        return False, "must start with 0x"
    if len(key) != 66:
        return False, f"expected 66 chars, got {len(key)}"
    hex_part = key[2:]
    if not all(c in "0123456789abcdefABCDEF" for c in hex_part):
        return False, "non-hex characters after 0x"
    return True, "valid 0x + 64-hex format"


def check_bridge_contract_exists(source_url: str, contract_addr: str) -> tuple[bool, str]:
    """Verify bridge contract has deployed bytecode on the source chain."""
    try:
        resp = _rpc_call(source_url, "eth_getCode", [contract_addr, "latest"])
        code = resp.get("result", "0x")
        # "0x" means no code (EOA or non-existent), anything longer means deployed
        if not isinstance(code, str) or len(code) <= 2:
            return False, f"no bytecode at {contract_addr} (got {code!r})"
        byte_count = (len(code) - 2) // 2
        return True, f"contract present, {byte_count} bytes of code"
    except urllib.error.URLError as exc:
        return False, f"connection error: {exc.reason}"
    except (ValueError, KeyError) as exc:
        return False, f"bad response: {exc}"


def check_registry_version(dest_url: str, registry_addr: str) -> tuple[bool, str]:
    """Call version() on the destination registry and confirm it returns a uint256."""
    call_obj = {
        "to": registry_addr,
        "data": VERSION_SELECTOR,
    }
    try:
        resp = _rpc_call(dest_url, "eth_call", [call_obj, "latest"])
        result = resp.get("result", "0x")
        if not isinstance(result, str) or result in ("0x", "0x" + "0" * 64):
            # A zeroed-out 32-byte word is suspicious — contract may not implement version()
            if result == "0x" + "0" * 64:
                # Zero is technically valid for version 0, but warn
                return True, "version() returned 0 (contract present, version=0)"
            return False, f"version() returned no data: {result!r}"
        version_int = int(result, 16)
        return True, f"registry live, version={version_int}"
    except urllib.error.URLError as exc:
        return False, f"connection error: {exc.reason}"
    except (ValueError, KeyError) as exc:
        return False, f"bad response: {exc}"


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main() -> int:
    """Run all pre-flight checks and print a checklist. Returns exit code."""
    print("ETP Gateway VM — Pre-flight Checks")
    print("=" * 50)

    results: list[tuple[bool, str, str]] = []  # (passed, label, detail)

    # --- Check 1: required env vars ---
    passed, detail = check_required_env_vars()
    results.append((passed, "Required env vars set", detail))

    # Collect env values now (may be empty strings if check 1 failed)
    source_url = os.environ.get("ETP_GATEWAY_VM_SOURCE_RPC_URL", "")
    bridge_contract = os.environ.get("ETP_GATEWAY_VM_SOURCE_BRIDGE_CONTRACT", "")
    dest_url = os.environ.get("ETP_GATEWAY_VM_DEST_RPC_URL", "")
    registry_addr = os.environ.get("ETP_GATEWAY_VM_DEST_REGISTRY", "")
    operator_key = os.environ.get("ETP_GATEWAY_VM_OPERATOR_KEY", "")

    # --- Check 2: source RPC reachable ---
    if source_url:
        passed, detail = check_source_rpc_reachable(source_url)
    else:
        passed, detail = False, "ETP_GATEWAY_VM_SOURCE_RPC_URL not set"
    results.append((passed, "Source RPC reachable", detail))

    # --- Check 3: dest RPC reachable ---
    if dest_url:
        passed, detail = check_dest_rpc_reachable(dest_url)
    else:
        passed, detail = False, "ETP_GATEWAY_VM_DEST_RPC_URL not set"
    results.append((passed, "Dest RPC reachable", detail))

    # --- Check 4: operator key format ---
    if operator_key:
        passed, detail = check_operator_key_format(operator_key)
    else:
        passed, detail = False, "ETP_GATEWAY_VM_OPERATOR_KEY not set"
    results.append((passed, "Operator key format valid", detail))

    # --- Check 5: bridge contract exists on source ---
    if source_url and bridge_contract:
        passed, detail = check_bridge_contract_exists(source_url, bridge_contract)
    else:
        passed, detail = False, "source URL or bridge contract address not set"
    results.append((passed, "Bridge contract exists on source", detail))

    # --- Check 6: registry version() on dest ---
    if dest_url and registry_addr:
        passed, detail = check_registry_version(dest_url, registry_addr)
    else:
        passed, detail = False, "dest URL or registry address not set"
    results.append((passed, "Registry contract live on dest", detail))

    # --- Print results ---
    print()
    all_passed = True
    for check_passed, label, detail in results:
        symbol = "[+]" if check_passed else "[x]"
        status = "PASS" if check_passed else "FAIL"
        print(f"  {symbol} {status}  {label}")
        print(f"         {detail}")
        if not check_passed:
            all_passed = False

    print()
    if all_passed:
        print("All checks passed. Safe to start the Gateway VM container.")
        return 0
    else:
        fail_count = sum(1 for p, _, _ in results if not p)
        print(f"{fail_count} check(s) failed. Fix the issues above before starting the container.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
