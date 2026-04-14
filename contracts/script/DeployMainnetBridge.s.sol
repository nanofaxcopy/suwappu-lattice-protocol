// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import {OptimisticBridgeChallenge} from "../src/OptimisticBridgeChallenge.sol";
import {ZKBridgeVerifier} from "../src/ZKBridgeVerifier.sol";

/// @title DeployMainnetBridge
/// @notice Production deployment of bridge contracts with enforced minimums.
///
/// TURNKEY: The node team runs this script when mainnet is ready.
/// Enforces production-grade parameters — testnet values are rejected.
///
/// Required environment variables:
///   MAINNET_TIMELOCK_ADMIN — Timelock address (24hr+ delay)
///
/// Optional (with production defaults):
///   MAINNET_CHALLENGE_PERIOD    — (default: 604800 = 7 days)
///   MAINNET_OPERATOR_BOND       — (default: 10 ether)
///   MAINNET_CHALLENGER_BOND     — (default: 1 ether)
///   MAINNET_ZK_MODE             — (default: 3 = STARK)
///
/// Usage:
///   forge script script/DeployMainnetBridge.s.sol:DeployMainnetBridge \
///     --rpc-url $MAINNET_RPC_URL \
///     --private-key $DEPLOYER_KEY \
///     --broadcast --verify -vvvv
contract DeployMainnetBridge is Script {
    function run() external {
        address timelockAdmin = vm.envAddress("MAINNET_TIMELOCK_ADMIN");
        uint256 challengePeriod = vm.envOr("MAINNET_CHALLENGE_PERIOD", uint256(604800));
        uint256 minOperatorBond = vm.envOr("MAINNET_OPERATOR_BOND", uint256(10 ether));
        uint256 minChallengerBond = vm.envOr("MAINNET_CHALLENGER_BOND", uint256(1 ether));
        uint8 zkMode = uint8(vm.envOr("MAINNET_ZK_MODE", uint256(3))); // STARK

        // Enforce mainnet minimums — reject testnet-grade parameters
        require(timelockAdmin != address(0), "MAINNET_TIMELOCK_ADMIN required");
        require(challengePeriod >= 86400, "Challenge period must be >= 1 day (86400s) for mainnet");
        require(minOperatorBond >= 1 ether, "Operator bond must be >= 1 ETH for mainnet");
        require(minChallengerBond >= 0.1 ether, "Challenger bond must be >= 0.1 ETH for mainnet");

        vm.startBroadcast();

        // 1. Deploy OptimisticBridgeChallenge with production bonds
        OptimisticBridgeChallenge challenge = new OptimisticBridgeChallenge(
            msg.sender,       // Temporary admin (transferred below)
            challengePeriod,
            minOperatorBond,
            minChallengerBond
        );

        // 2. Deploy ZKBridgeVerifier in STARK mode
        ZKBridgeVerifier zkVerifier = new ZKBridgeVerifier(
            msg.sender,
            address(challenge),
            zkMode
        );

        // 3. Wire ZK verifier into challenge contract
        challenge.setZKVerifier(address(zkVerifier));

        // 4. Transfer admin to Timelock (irreversible governance handoff)
        challenge.transferAdmin(timelockAdmin);
        zkVerifier.transferAdmin(timelockAdmin);

        vm.stopBroadcast();

        // Summary
        console.log("=== Mainnet Bridge Deployment ===");
        console.log("OptimisticBridgeChallenge:", address(challenge));
        console.log("ZKBridgeVerifier:", address(zkVerifier));
        console.log("Admin (Timelock):", timelockAdmin);
        console.log("Challenge period:", challengePeriod, "seconds");
        console.log("Operator bond:", minOperatorBond / 1 ether, "ETH");
        console.log("Challenger bond:", minChallengerBond / 1 ether, "ETH");
        console.log("ZK mode:", zkMode, "(3=STARK)");
        console.log("Chain ID:", block.chainid);
    }
}
