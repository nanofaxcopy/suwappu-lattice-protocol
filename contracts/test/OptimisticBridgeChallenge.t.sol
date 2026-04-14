// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/OptimisticBridgeChallenge.sol";

contract OptimisticBridgeChallengeTest is Test {
    OptimisticBridgeChallenge public challenge;

    address admin = address(0xAD);
    address operator = address(0xBEEF);
    address challenger = address(0xCAFE);

    uint256 constant PERIOD = 7 days;
    uint256 constant OP_BOND = 0.01 ether;
    uint256 constant CH_BOND = 0.001 ether;

    bytes32 constant DIGEST_1 = keccak256("anchor-digest-1");
    bytes32 constant DIGEST_2 = keccak256("anchor-digest-2");
    bytes32 constant PROOF_HASH = keccak256("fraud-proof-data");

    function setUp() public {
        challenge = new OptimisticBridgeChallenge(admin, PERIOD, OP_BOND, CH_BOND);
        vm.deal(operator, 10 ether);
        vm.deal(challenger, 10 ether);
    }

    // -----------------------------------------------------------------------
    // openWindow
    // -----------------------------------------------------------------------

    function test_openWindow_success() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        OptimisticBridgeChallenge.Challenge memory c = challenge.getChallenge(DIGEST_1);
        assertEq(c.opener, operator);
        assertEq(c.status, challenge.STATUS_OPEN());
        assertEq(c.operatorBond, OP_BOND);
        assertEq(c.deadline, uint64(block.timestamp + PERIOD));
    }

    function test_openWindow_rejectsDuplicateDigest() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        vm.prank(operator);
        vm.expectRevert(OptimisticBridgeChallenge.WindowAlreadyExists.selector);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);
    }

    function test_openWindow_rejectsZeroDigest() public {
        vm.prank(operator);
        vm.expectRevert(OptimisticBridgeChallenge.ZeroDigest.selector);
        challenge.openWindow{value: OP_BOND}(bytes32(0));
    }

    function test_openWindow_rejectsInsufficientBond() public {
        vm.prank(operator);
        vm.expectRevert(
            abi.encodeWithSelector(
                OptimisticBridgeChallenge.InsufficientBond.selector,
                OP_BOND,
                OP_BOND - 1
            )
        );
        challenge.openWindow{value: OP_BOND - 1}(DIGEST_1);
    }

    function test_openWindow_emitsEvent() public {
        vm.prank(operator);
        vm.expectEmit(true, true, false, true);
        emit OptimisticBridgeChallenge.WindowOpened(
            DIGEST_1, operator, OP_BOND, uint64(block.timestamp + PERIOD)
        );
        challenge.openWindow{value: OP_BOND}(DIGEST_1);
    }

    // -----------------------------------------------------------------------
    // submitChallenge
    // -----------------------------------------------------------------------

    function test_submitChallenge_success() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        vm.prank(challenger);
        challenge.submitChallenge{value: CH_BOND}(DIGEST_1, 1, PROOF_HASH);

        OptimisticBridgeChallenge.Challenge memory c = challenge.getChallenge(DIGEST_1);
        assertEq(c.status, challenge.STATUS_CHALLENGED());
        assertEq(c.challenger, challenger);
        assertEq(c.fraudProofHash, PROOF_HASH);
        assertEq(c.fraudProofType, 1);
        assertEq(c.challengerBond, CH_BOND);
    }

    function test_submitChallenge_rejectsIfNotOpen() public {
        vm.prank(challenger);
        vm.expectRevert(OptimisticBridgeChallenge.WindowNotOpen.selector);
        challenge.submitChallenge{value: CH_BOND}(DIGEST_1, 1, PROOF_HASH);
    }

    function test_submitChallenge_rejectsAfterDeadline() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        vm.warp(block.timestamp + PERIOD + 1);

        vm.prank(challenger);
        vm.expectRevert(OptimisticBridgeChallenge.ChallengeDeadlinePassed.selector);
        challenge.submitChallenge{value: CH_BOND}(DIGEST_1, 1, PROOF_HASH);
    }

    function test_submitChallenge_rejectsInsufficientBond() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        vm.prank(challenger);
        vm.expectRevert(
            abi.encodeWithSelector(
                OptimisticBridgeChallenge.InsufficientBond.selector,
                CH_BOND,
                0
            )
        );
        challenge.submitChallenge{value: 0}(DIGEST_1, 1, PROOF_HASH);
    }

    // -----------------------------------------------------------------------
    // resolveChallenge
    // -----------------------------------------------------------------------

    function test_resolveChallenge_validFraud_slashesOperator() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        vm.prank(challenger);
        challenge.submitChallenge{value: CH_BOND}(DIGEST_1, 1, PROOF_HASH);

        uint256 challengerBalBefore = challenger.balance;

        vm.prank(admin);
        challenge.resolveChallenge(DIGEST_1, true);

        OptimisticBridgeChallenge.Challenge memory c = challenge.getChallenge(DIGEST_1);
        assertEq(c.status, challenge.STATUS_RESOLVED());
        // Challenger receives both bonds
        assertEq(challenger.balance, challengerBalBefore + OP_BOND + CH_BOND);
    }

    function test_resolveChallenge_invalidChallenge_slashesChallenger() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        vm.prank(challenger);
        challenge.submitChallenge{value: CH_BOND}(DIGEST_1, 1, PROOF_HASH);

        uint256 operatorBalBefore = operator.balance;

        vm.prank(admin);
        challenge.resolveChallenge(DIGEST_1, false);

        assertEq(operator.balance, operatorBalBefore + OP_BOND + CH_BOND);
    }

    function test_resolveChallenge_onlyAdmin() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        vm.prank(challenger);
        challenge.submitChallenge{value: CH_BOND}(DIGEST_1, 1, PROOF_HASH);

        vm.prank(challenger);
        vm.expectRevert(OptimisticBridgeChallenge.Unauthorized.selector);
        challenge.resolveChallenge(DIGEST_1, true);
    }

    function test_resolveChallenge_rejectsIfNotChallenged() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        vm.prank(admin);
        vm.expectRevert(OptimisticBridgeChallenge.WindowNotChallenged.selector);
        challenge.resolveChallenge(DIGEST_1, true);
    }

    // -----------------------------------------------------------------------
    // finalizeWindow
    // -----------------------------------------------------------------------

    function test_finalizeWindow_afterDeadline() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        vm.warp(block.timestamp + PERIOD + 1);

        uint256 operatorBalBefore = operator.balance;
        challenge.finalizeWindow(DIGEST_1);

        OptimisticBridgeChallenge.Challenge memory c = challenge.getChallenge(DIGEST_1);
        assertEq(c.status, challenge.STATUS_FINALIZED());
        assertEq(operator.balance, operatorBalBefore + OP_BOND);
    }

    function test_finalizeWindow_rejectsBeforeDeadline() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        vm.expectRevert(OptimisticBridgeChallenge.WindowNotExpired.selector);
        challenge.finalizeWindow(DIGEST_1);
    }

    function test_finalizeWindow_rejectsIfNotOpen() public {
        vm.expectRevert(OptimisticBridgeChallenge.WindowNotOpen.selector);
        challenge.finalizeWindow(DIGEST_1);
    }

    // -----------------------------------------------------------------------
    // View functions
    // -----------------------------------------------------------------------

    function test_isFinalized() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        assertFalse(challenge.isFinalized(DIGEST_1));

        vm.warp(block.timestamp + PERIOD + 1);
        challenge.finalizeWindow(DIGEST_1);

        assertTrue(challenge.isFinalized(DIGEST_1));
    }

    function test_isChallenged() public {
        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_1);

        assertFalse(challenge.isChallenged(DIGEST_1));

        vm.prank(challenger);
        challenge.submitChallenge{value: CH_BOND}(DIGEST_1, 1, PROOF_HASH);

        assertTrue(challenge.isChallenged(DIGEST_1));
    }

    // -----------------------------------------------------------------------
    // Admin functions
    // -----------------------------------------------------------------------

    function test_setChallengePeriod() public {
        vm.prank(admin);
        challenge.setChallengePeriod(14 days);
        assertEq(challenge.challengePeriod(), 14 days);
    }

    function test_transferAdmin() public {
        vm.prank(admin);
        challenge.transferAdmin(address(0xDEAD));
        assertEq(challenge.admin(), address(0xDEAD));
    }

    // -----------------------------------------------------------------------
    // Fuzz tests
    // -----------------------------------------------------------------------

    function test_fuzz_openWindowBondAmount(uint256 bond) public {
        vm.assume(bond >= OP_BOND && bond <= 100 ether);
        vm.deal(operator, bond);

        vm.prank(operator);
        challenge.openWindow{value: bond}(keccak256(abi.encode(bond)));

        OptimisticBridgeChallenge.Challenge memory c = challenge.getChallenge(keccak256(abi.encode(bond)));
        assertEq(c.operatorBond, bond);
    }

    function test_fuzz_challengePeriodTiming(uint256 warpTime) public {
        vm.assume(warpTime > 0 && warpTime <= 365 days);

        vm.prank(operator);
        challenge.openWindow{value: OP_BOND}(DIGEST_2);

        vm.warp(block.timestamp + warpTime);

        if (warpTime > PERIOD) {
            // Should be finalizable
            challenge.finalizeWindow(DIGEST_2);
            assertTrue(challenge.isFinalized(DIGEST_2));
        } else {
            // Should NOT be finalizable
            vm.expectRevert(OptimisticBridgeChallenge.WindowNotExpired.selector);
            challenge.finalizeWindow(DIGEST_2);
        }
    }
}
