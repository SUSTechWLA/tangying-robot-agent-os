# V1 readiness and runtime hardening

**Goal:** Evaluate the existing simulation-to-real architecture against executable evidence, fix reproduced runtime defects, and publish an honest V1 release assessment.

**Architecture:** Keep the existing Task/Revision → Edge → RobotRuntime → backend boundary. Harden command admission, durable execution state and local safety without adding unvalidated hardware motion.

**Constraints:** Preserve existing uncommitted Fleet/Web changes. No physical robot is available. No production deployment or hardware certification is implied. Use the current branch and workspace so all existing changes are included in verification.

## Work items

- [x] Inspect architecture, deployment, simulation and physical adapter contracts; record baseline tests.
- [x] Reproduce concurrent command execution and interrupted-command replay in `robot/gateway/tests/test_service.py`; fix admission and write-ahead state in `service.py` / `journal.py`; verify duplicates do not call the backend.
- [x] Reproduce safety lifecycle defects in `test_safety.py`; persist failed stops, enforce active deadlines, serialize stop/reset transitions.
- [x] Reproduce malformed action/configuration handling in driver/backend tests; reject unsafe inputs before actuator calls.
- [x] Exercise production readiness CLI with invalid providers/configuration and JSON consumers; fix false readiness and structured output.
- [x] Run Python/Go/Web suites, static checks, build, current simulation acceptance and available RoboCasa checks; distinguish skipped dependencies and retained evidence from live runs.
- [x] Publish `docs/production/v1-assessment-2026-09-05.md` with verified capability matrix, fixes, release evidence and concrete hardware/production blockers.

## Final outcome

Current-checkout RoboCasa-inclusive Python verification: 627 passed, 27 skipped, no failures. Web: 119 passed. Go: 42 tested packages passed; race: 27 tested packages passed. Build, vet and lint passed. Simulation: 30/30 episodes, 18/18 object/target goals, two-goal sequence succeeded. Fixed an additional deterministic lifecycle lock-budget defect and strengthened the checkpoint test to require post-restart fresh evidence.

After the full suite, fixed a demo-only process leak by launching the compiled Local Agent binary directly instead of its `go run` wrapper. The real-process regression first failed, then all 4 demo tests passed; shell syntax, lint and diff checks also passed. The full suite was not repeated after this isolated shell-script change.

Physical production readiness remains NO-GO: hardware/providers/upstream compatibility and distributed durability/HA are outstanding. Retained round4 signature/files are intact but current Web changes invalidate its frontend match; a newly audited browser capture is still required. No real robot or production deployment was performed.

## Verification

Use failing regression tests before each behavior change. Run focused gateway/driver/install tests after fixes; use `make test-python`, `make test-go`, `npm test --prefix web`, `make lint`, `make build`, `bash scripts/demo.sh`, and `scripts/run_simulation_acceptance.py` for integration. Revalidate retained RoboCasa evidence separately from current live tests. Never fabricate hardware provider or acceptance evidence.
