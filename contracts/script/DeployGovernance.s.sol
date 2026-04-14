// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import {ETPGovernance} from "../src/ETPGovernance.sol";

/// @title DeployGovernance
/// @notice Deploys ETPGovernance contract and transfers admin to Timelock.
///
/// Required env vars:
///   GOVERNANCE_ADMIN — Timelock address for governance handoff
///
/// Optional:
///   GOVERNANCE_REQUIRED_RATIO — Supermajority in basis points (default: 6667 = 66.67%)
contract DeployGovernance is Script {
    function run() external {
        address timelockAdmin = vm.envAddress("GOVERNANCE_ADMIN");
        uint256 requiredRatio = vm.envOr("GOVERNANCE_REQUIRED_RATIO", uint256(6667));

        require(timelockAdmin != address(0), "GOVERNANCE_ADMIN required");

        vm.startBroadcast();

        // Deploy with deployer as initial admin
        ETPGovernance governance = new ETPGovernance(msg.sender, requiredRatio);

        // Transfer admin to Timelock
        governance.transferAdmin(timelockAdmin);

        vm.stopBroadcast();

        console.log("=== Governance Deployment ===");
        console.log("ETPGovernance:", address(governance));
        console.log("Admin (Timelock):", timelockAdmin);
        console.log("Required ratio:", requiredRatio, "basis points");
        console.log("Initial phase: BOOTSTRAP");
        console.log("Chain ID:", block.chainid);
    }
}
