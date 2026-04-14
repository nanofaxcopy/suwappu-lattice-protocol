// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import {ETPGovernance} from "../src/ETPGovernance.sol";

/// @title DeployMainnetGovernance
/// @notice Production deployment of ETPGovernance for on-chain phase transitions.
///
/// TURNKEY: The node team runs this script when mainnet governance is ready.
///
/// Required environment variables:
///   MAINNET_TIMELOCK_ADMIN — Timelock address (governance-controlled)
///
/// Optional:
///   GOVERNANCE_REQUIRED_RATIO — Supermajority in basis points (default: 6667 = 66.67%)
///
/// Usage:
///   forge script script/DeployMainnetGovernance.s.sol:DeployMainnetGovernance \
///     --rpc-url $MAINNET_RPC_URL \
///     --private-key $DEPLOYER_KEY \
///     --broadcast --verify -vvvv
contract DeployMainnetGovernance is Script {
    function run() external {
        address timelockAdmin = vm.envAddress("MAINNET_TIMELOCK_ADMIN");
        uint256 requiredRatio = vm.envOr("GOVERNANCE_REQUIRED_RATIO", uint256(6667));

        require(timelockAdmin != address(0), "MAINNET_TIMELOCK_ADMIN required");
        require(requiredRatio >= 5000 && requiredRatio <= 10000,
            "Required ratio must be 50-100% (5000-10000 basis points)");

        vm.startBroadcast();

        // Deploy with deployer as initial admin
        ETPGovernance governance = new ETPGovernance(msg.sender, requiredRatio);

        // Transfer admin to Timelock
        governance.transferAdmin(timelockAdmin);

        vm.stopBroadcast();

        console.log("=== Mainnet Governance Deployment ===");
        console.log("ETPGovernance:", address(governance));
        console.log("Admin (Timelock):", timelockAdmin);
        console.log("Required ratio:", requiredRatio, "basis points");
        console.log("Initial phase: BOOTSTRAP");
        console.log("Chain ID:", block.chainid);
    }
}
