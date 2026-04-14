// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import {LTPAnchorRegistry} from "../src/LTPAnchorRegistry.sol";

/// @title UpgradeRegistry
/// @notice UUPS upgrade script for LTPAnchorRegistry.
///
/// TURNKEY: Deploys a new implementation and logs the upgrade calldata.
/// The actual upgrade must be executed through the governance path:
///   MultiSig -> Timelock -> Registry.upgradeToAndCall()
///
/// Required environment variables:
///   MAINNET_PROXY_ADDRESS   — Existing LTPAnchorRegistry proxy address
///
/// Usage:
///   forge script script/UpgradeRegistry.s.sol:UpgradeRegistry \
///     --rpc-url $MAINNET_RPC_URL \
///     --private-key $DEPLOYER_KEY \
///     --broadcast -vvvv
///
/// After running:
///   1. Note the new implementation address from the logs
///   2. Submit upgradeToAndCall(newImpl, "") through MultiSig -> Timelock
///   3. Verify: cast call $PROXY "version()(uint256)" --rpc-url $RPC
contract UpgradeRegistry is Script {
    function run() external {
        address proxyAddress = vm.envAddress("MAINNET_PROXY_ADDRESS");
        require(proxyAddress != address(0), "MAINNET_PROXY_ADDRESS required");

        vm.startBroadcast();

        // 1. Deploy new implementation
        LTPAnchorRegistry newImpl = new LTPAnchorRegistry();

        vm.stopBroadcast();

        // 2. Log upgrade instructions
        console.log("=== Registry Upgrade ===");
        console.log("New implementation:", address(newImpl));
        console.log("Proxy:", proxyAddress);
        console.log("Chain ID:", block.chainid);
        console.log("");
        console.log("--- Next steps (governance path) ---");
        console.log("1. Encode: upgradeToAndCall(", address(newImpl), ", bytes(''))");
        console.log("2. Submit via MultiSig -> Timelock -> Proxy");
        console.log("3. Wait for Timelock delay");
        console.log("4. Execute via MultiSig -> Timelock");
        console.log("5. Verify: cast call", proxyAddress, "version()(uint256)");
    }
}
