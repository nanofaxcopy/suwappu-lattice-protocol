// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import {OptimisticBridgeChallenge} from "../src/OptimisticBridgeChallenge.sol";
import {ZKBridgeVerifier} from "../src/ZKBridgeVerifier.sol";

/// @title DeployBridge
/// @notice Deploys OptimisticBridgeChallenge + ZKBridgeVerifier, wires them
///         together, then transfers admin to the Timelock for governance.
///
/// Required environment variables:
///   BRIDGE_TIMELOCK_ADMIN     — Timelock address (governance-controlled admin)
///
/// Optional environment variables:
///   BRIDGE_CHALLENGE_PERIOD   — Challenge window in seconds (default: 3600 for testnet)
///   BRIDGE_MIN_OPERATOR_BOND  — Minimum operator bond in wei (default: 0 for testnet)
///   BRIDGE_MIN_CHALLENGER_BOND — Minimum challenger bond in wei (default: 0 for testnet)
///   BRIDGE_ZK_MODE            — ZK verification mode: 0=SIMULATED, 3=STARK (default: 0)
///
/// Architecture:
///   OptimisticBridgeChallenge (challenge windows, bond mechanics)
///       ↕ setZKVerifier / finalizeWithZKProof
///   ZKBridgeVerifier (proof verification, instant finality)
///       ↓ admin
///   TimelockController (governance)
contract DeployBridge is Script {
    function run() external {
        // ---- Read configuration from environment ----
        address timelockAdmin = vm.envAddress("BRIDGE_TIMELOCK_ADMIN");
        uint256 challengePeriod = vm.envOr("BRIDGE_CHALLENGE_PERIOD", uint256(3600));
        uint256 minOperatorBond = vm.envOr("BRIDGE_MIN_OPERATOR_BOND", uint256(0));
        uint256 minChallengerBond = vm.envOr("BRIDGE_MIN_CHALLENGER_BOND", uint256(0));
        uint8 zkMode = uint8(vm.envOr("BRIDGE_ZK_MODE", uint256(0)));

        require(timelockAdmin != address(0), "BRIDGE_TIMELOCK_ADMIN required");

        vm.startBroadcast();

        // 1. Deploy OptimisticBridgeChallenge (deployer as initial admin)
        OptimisticBridgeChallenge challenge = new OptimisticBridgeChallenge(
            msg.sender,
            challengePeriod,
            minOperatorBond,
            minChallengerBond
        );

        // 2. Deploy ZKBridgeVerifier (deployer as initial admin, pointing to challenge)
        ZKBridgeVerifier zkVerifier = new ZKBridgeVerifier(
            msg.sender,
            address(challenge),
            zkMode
        );

        // 3. Wire: authorize ZK verifier on challenge contract
        challenge.setZKVerifier(address(zkVerifier));

        // 4. Transfer admin to Timelock (irreversible governance handoff)
        challenge.transferAdmin(timelockAdmin);
        zkVerifier.transferAdmin(timelockAdmin);

        vm.stopBroadcast();

        // ---- Deployment summary ----
        console.log("=== Bridge Deployment ===");
        console.log("OptimisticBridgeChallenge:", address(challenge));
        console.log("ZKBridgeVerifier:", address(zkVerifier));
        console.log("Admin (Timelock):", timelockAdmin);
        console.log("Challenge period:", challengePeriod, "seconds");
        console.log("Min operator bond:", minOperatorBond);
        console.log("Min challenger bond:", minChallengerBond);
        console.log("ZK mode:", zkMode);
        console.log("Chain ID:", block.chainid);
    }
}
