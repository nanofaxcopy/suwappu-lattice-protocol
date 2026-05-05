"""Gateway VM — CLI entry point.

Usage:
    python -m src.ltp.gateway_vm
    etp-gateway  (if installed via pyproject.toml scripts)

Requires: pip install -e ".[gateway,chain,crypto]"
"""

from __future__ import annotations

import os
import sys

from .boot import validate_config
from .config import GatewayVMConfig


def main() -> None:
    """Boot the gateway VM process.

    1. Load config from ETP_GATEWAY_VM_* env vars
    2. Validate required fields (fail-fast)
    3. Create real Web3/RPC callables for source and dest chains
    4. Create DevnetAnchorClient for on-chain anchoring
    5. Build GatewayVMService + GatewayTracker
    6. Create FastAPI app via create_app()
    7. Run uvicorn
    """
    config = GatewayVMConfig.from_env()

    # --- Fail-fast validation ---
    missing = validate_config(config)
    if missing:
        print(
            f"ERROR: Missing required env vars:\n  " + "\n  ".join(missing),
            file=sys.stderr,
        )
        sys.exit(1)

    operator_key = os.environ.get("ETP_GATEWAY_VM_OPERATOR_KEY", "")
    if not operator_key:
        print("ERROR: Missing ETP_GATEWAY_VM_OPERATOR_KEY", file=sys.stderr)
        sys.exit(1)

    # --- Late imports (web3 + crypto may not be installed) ---
    try:
        from web3 import Web3
    except ImportError:
        print(
            'ERROR: web3 not installed. Run: pip install -e ".[gateway,chain,crypto]"',
            file=sys.stderr,
        )
        sys.exit(1)

    from ..keypair import KeyPair
    from .anchor_client import DevnetAnchorClient
    from .app import create_app
    from .service import GatewayVMService
    from .tracker import GatewayTracker

    # --- Source chain RPC ---
    w3_source = Web3(Web3.HTTPProvider(config.source_rpc_url))

    def fetch_logs(from_block: int, to_block: int) -> list[dict]:
        return w3_source.eth.get_logs(
            {
                "fromBlock": from_block,
                "toBlock": to_block,
                "address": config.source_bridge_contract,
            }
        )

    def get_source_block_number() -> int:
        return w3_source.eth.block_number

    # --- Dest chain RPC ---
    w3_dest = Web3(Web3.HTTPProvider(config.dest_rpc_url))

    def get_dest_block_number() -> int:
        return w3_dest.eth.block_number

    # --- Anchor client ---
    anchor_client = DevnetAnchorClient.from_gateway_config(config, operator_key)

    # --- Operator keypair (ML-DSA-65 for attestation signing) ---
    keypair = KeyPair.generate(config.gateway_id)

    # --- Build service ---
    tracker = GatewayTracker()
    service = GatewayVMService(
        config=config,
        operator_keypair=keypair,
        fetch_logs=fetch_logs,
        get_source_block_number=get_source_block_number,
        get_dest_block_number=get_dest_block_number,
        anchor_fn=anchor_client.as_anchor_fn(),
        is_signer_authorized=lambda: True,
    )

    # --- Create FastAPI app and run ---
    app = create_app(config, service, tracker)

    import uvicorn

    host = os.environ.get("ETP_GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("ETP_GATEWAY_PORT", "8000"))

    print(f"Starting ETP Gateway VM on {host}:{port}")
    print(f"  Gateway ID:   {config.gateway_id}")
    print(f"  Source chain:  {config.source_chain_id}")
    print(f"  Dest chain:    {config.dest_chain_id}")
    print(f"  Challenge:     {config.challenge_mode}")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
