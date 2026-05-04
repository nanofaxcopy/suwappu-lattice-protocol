# Gateway VM Execution Roadmap

**Author:** Javier Calderon Jr, CTO - Global Settlement (GSX)

**Date:** May 4, 2026

**Purpose:** Execution order, dependencies, and decision gates for the four Gateway VM implementation plans. This roadmap must be followed before any code is written.

---

## Plan Inventory

| Plan | File | Phase | Tasks | Tests | LOC |
|---|---|---|---|---|---|
| 1 — Gateway VM Core | `2026-05-04-gateway-vm-core.md` | Phase 1 | 14 | ~62 | ~1,100 |
| 2 — Gateway Transaction Flow | `2026-05-04-gateway-tx-flow.md` | Phase 2 | 8 | ~28 | ~700 |
| 3 — Gateway Stress Testing | `2026-05-04-gateway-stress-testing.md` | Phase 3 | 10 | ~29 | ~700 |
| 4 — Dual VM Introduction | `2026-05-04-dual-vm-introduction.md` | Phase 4 | 9 | ~47 | ~1,200 |
| **Total** | | | **41** | **~166** | **~3,700** |

---

## Execution Order

```
Plan 1: Gateway VM Core (Phase 1)
│
│  Tasks 1-10: Core module (domain tags, config, BridgeEvent, ReplayDB,
│              EventValidator, AttestationWriter, EventListener, metrics,
│              GatewayVMService daemon, regression)
│
│  Tasks 11-14: Audit addendum (FinalityWatcher, ChallengeManager,
│               StructuredLogger, GatewayVM entry point)
│
├─── GATE 1: All Plan 1 tests green. Module imports cleanly. ───┐
│                                                                │
▼                                                                │
Plan 2: Gateway Transaction Flow (Phase 2)                       │
│                                                                │
│  Tasks 1-4: DevnetAnchorClient, GatewayTracker,               │
│             REST endpoints (status, health, events)            │
│                                                                │
│  Tasks 5-6: E2E integration + bidirectional flow tests         │
│                                                                │
│  Tasks 7-8: Grafana dashboard, alerts, full regression         │
│                                                                │
├─── GATE 2: E2E pipeline proven with mock RPC. ────────────────┐
│    REST endpoints responding. Dashboard template ready.        │
│                                                                │
▼                                                                │
Plan 3: Gateway Stress Testing (Phase 3)                         │
│                                                                │
│  Tasks 1-8: All 15 adversarial scenarios                      │
│             (replay, reorg, RPC failure, signer revocation,    │
│              bad payloads, ordering, challenges, multi-gateway, │
│              crash recovery)                                   │
│                                                                │
│  Task 9: Throughput benchmark (100 events/min target)          │
│                                                                │
│  Task 10: Full regression                                      │
│                                                                │
├─── GATE 3: All 15 scenarios pass. 100 events/min sustained. ──┐
│    No duplicate anchors under any scenario. Full audit trail.  │
│                                                                │
│    >>> DECISION POINT: Deploy single-VM gateway to live <<<    │
│    >>> testnet or proceed to Phase 4?                 <<<      │
│                                                                │
▼                                                                │
Plan 4: Dual VM Introduction (Phase 4)                           │
│                                                                │
│  Tasks 1-3: Config, WriterRegistry, MoveTransactionFilter     │
│  Tasks 4-5: BLS keys, BLS attestation                         │
│  Tasks 6-7: State deltas, dual root, precompile               │
│  Tasks 8-9: State verifier, equivocation, package exports     │
│                                                                │
└─── GATE 4: Dual VM infrastructure proven. ─────────────────────┘
     Writer permissioning, BLS attestation, state propagation,
     precompile interface all tested.

     >>> Next: Proposed MoveVM+DID Architecture (separate plan)
     >>> Then: DID Expansion Plan (built in tandem)
```

---

## Gate Criteria

### Gate 1 — Plan 1 Complete

| Check | Command | Expected |
|---|---|---|
| All gateway VM tests pass | `pytest tests/test_gateway_vm_*.py -v` | All PASS |
| Module imports cleanly | `python -c "from src.ltp.gateway_vm import *"` | No errors |
| Domain tags collision-free | `python -c "from src.ltp.domain import DOMAIN_GATEWAY_ATTEST"` | No collision |
| No regressions | `pytest tests/ -v --tb=short` | All existing tests PASS |
| Entry point lifecycle works | `pytest tests/test_gateway_vm_main.py -v` | All PASS |

**Decision:** Proceed to Plan 2. No code deployment needed.

### Gate 2 — Plan 2 Complete

| Check | Command | Expected |
|---|---|---|
| E2E pipeline passes | `pytest tests/test_gateway_vm_e2e.py -v` | All PASS |
| Bidirectional flows work | `pytest tests/test_gateway_vm_bidirectional.py -v` | All PASS |
| REST endpoints respond | `pytest tests/test_gateway_vm_routes.py -v` | All PASS |
| Dashboard template valid | JSON lint on `etp-gateway.json` | Valid JSON |
| No regressions | `pytest tests/ -v --tb=short` | All existing tests PASS |

**Decision:** Proceed to Plan 3. Consider deploying REST endpoints to staging.

### Gate 3 — Plan 3 Complete

| Check | Command | Expected |
|---|---|---|
| All 15 scenarios pass | `pytest tests/stress/ -v --timeout=120` | All PASS |
| Throughput target met | `pytest tests/stress/test_throughput.py -v` | 100+ events/min |
| No duplicate anchors | Verified by replay + multi-gateway tests | Zero duplicates |
| Full audit trail | Verified by structured logging tests | Correlation IDs present |
| No regressions | `pytest tests/ -v --tb=short` | All existing tests PASS |

**Decision point:** This is the first deployment-ready gate.

- **Option A:** Deploy single-VM gateway to GSX devnet for live testing. Run against real Base Sepolia bridge events. Validate with real RPC, real finality delays, real gas costs. Then proceed to Plan 4.
- **Option B:** Proceed directly to Plan 4 (dual VM) before any live deployment.
- **Recommended:** Option A. Live testnet validation is the whole point of Phases 1-3. Phase 4 introduces MoveVM — proving the single-VM gateway first reduces risk surface.

### Gate 4 — Plan 4 Complete

| Check | Command | Expected |
|---|---|---|
| All dual VM tests pass | `pytest tests/test_dual_vm_*.py -v` | All PASS |
| Writer enforcement deterministic | Transaction filter tests | Unauthorized = no-op |
| BLS aggregation works | BLS key + attestation tests | Aggregate verify passes |
| Equivocation detected | State verifier tests | Read halted on mismatch |
| No regressions | `pytest tests/ -v --tb=short` | All existing tests PASS |

**Decision:** Dual VM infrastructure proven. Proceed to Proposed MoveVM+DID Architecture implementation plan (separate brainstorming + planning cycle).

---

## Critical Path

The critical path is sequential — each plan depends on the previous:

```
Plan 1 (14 tasks, ~62 tests)
  ↓ Gate 1
Plan 2 (8 tasks, ~28 tests)
  ↓ Gate 2
Plan 3 (10 tasks, ~29 tests)
  ↓ Gate 3 + DEPLOYMENT DECISION
Plan 4 (9 tasks, ~47 tests)
  ↓ Gate 4
MoveVM+DID Architecture (separate plan)
  ↓
DID Expansion Plan (in tandem)
```

No parallelism between plans — each builds on the previous. Within each plan, tasks are sequential (TDD: test → implement → verify → commit).

---

## Open Questions That Must Be Resolved Before Execution

### Before Plan 1 Starts

None. Plan 1 uses only existing infrastructure.

### Before Plan 2 Starts

| # | Question | Impact | Resolution Owner |
|---|---|---|---|
| 1 | Does `AnchorSubmission` dataclass exist in `src/ltp/anchor/client.py`? | Plan 2 Task 1 imports it | Verify before implementing `DevnetAnchorClient` |

### Before Plan 3 Starts

None. Plan 3 uses only Plan 1+2 infrastructure.

### Before Plan 4 Starts

| # | Question | Impact | Resolution Owner |
|---|---|---|---|
| 8 | MoveVM binary: Aptos Move, Sui Move, or independent? | Determines resource model semantics | Architecture decision |
| 9 | BLS library: blst, py_ecc, or other? | Determines Plan 4 Task 4 implementation | Engineering decision (blst recommended) |
| 11 | Writer registry contract: separate from LTPAnchorRegistry? | Determines governance surface | Architecture decision |

These are from the spec's Open Questions section. Plan 4 is designed so Tasks 1-8 work with the test-mode BLS fallback (HMAC simulation) and don't require MoveVM binary integration. The MoveVM binary decision (Q8) only matters for production deployment, not for the infrastructure tests.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `domain_hash_bytes` interface changes | Low | Plan 1 Task 6 breaks | Pinned in Plan 1 audit; function is stable |
| `blst` library unavailable on macOS ARM | Medium | Plan 4 Task 4 fallback needed | Test-mode HMAC fallback already in plan |
| AnchorSubmission dataclass shape differs | Medium | Plan 2 Task 1 needs adjustment | Verify interface before Plan 2 starts |
| SQLite WAL mode + concurrent access | Low | Plan 1 Task 4 edge case | Stress test in Plan 3 Scenario 14 covers this |
| MetricsRegistry label support differs | Low | Plan 1 Task 8 assertions fail | Verified in audit — labels work as expected |
| Existing gateway/ vs gateway_vm/ confusion | Medium | Import path collisions | Different packages, no shared names |
| MoveVM binary decision delayed | High | Plan 4 deployment blocked | Plan 4 infrastructure works without binary |

---

## Execution Rules

1. **No code before plans are approved.** All four plans must be reviewed and approved before any implementation begins.

2. **TDD strictly.** Every task: write test → run to verify failure → implement → run to verify pass → commit. No skipping steps.

3. **Gate before proceeding.** Run gate checks at each plan boundary. Do not start Plan N+1 until Gate N passes.

4. **One commit per task.** Each task produces one commit. Commit messages follow the `feat(gateway-vm):` / `test(stress):` / `feat(dual-vm):` convention.

5. **No scope creep.** Plans define exact deliverables. Additional features are new plans, not modifications to existing ones.

6. **Audit between plans.** After each plan completes, review the spec for drift. If the spec has changed, update the next plan before starting it.

---

## Document Sequencing (reminder)

```
This Roadmap
├── Plan 1: Gateway VM Core (Phase 1)
├── Plan 2: Gateway Transaction Flow (Phase 2)
├── Plan 3: Gateway Stress Testing (Phase 3)
└── Plan 4: Dual VM Introduction (Phase 4)
         ↓
Proposed MoveVM+DID Architecture (existing doc)
    Full identity system on dual-VM foundation
         ↓
DID Expansion Plan (existing doc)
    did:etp method, VCs, cross-chain resolution
```

Each plan produces independently testable software. Each gate verifies the previous plan's output before the next begins. The roadmap ensures we proceed delicately, validating each assumption before building on it.
