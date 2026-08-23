# Policy-Backed Tools, Recovery, and Sim2Real Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an auditable VLA/IL/RL policy-tool boundary, observation-gated safe recovery, user-readable policy/recovery progress, and deterministic dual-robot simulation acceptance without changing the proven AgentOS/Harness authority model.

**Architecture:** Run model inference in an optional Edge policy sidecar. The Edge Worker builds a canonical local observation bundle, requests a bounded action chunk, validates the policy manifest and response, and attaches the chunk to the existing semantic Robot Runtime command. Hardware-specific validation remains in the XLeRobot gateway and physical completion remains exclusively Harness/world-evidence based. Structured recovery events project into the existing versioned task experience and mission rail.

**Tech Stack:** Go 1.26, Python 3.11, HTTP/JSON sidecar protocol, gRPC Robot Runtime, MuJoCo/RoboCasa, vanilla JavaScript, Three.js, pytest, Node test runner.

**Spec:** `docs/superpowers/specs/2026-08-24-policy-tools-recovery-sim2real-design.md`

## Global constraints

- No policy provider may complete an intent, mint a lease, change a fencing token, or write authoritative world state.
- No physical action is retried after an unknown outcome without observation reconciliation.
- Model output is never accepted as Harness evidence.
- Existing deployments without `action_chunk` capabilities remain behavior-compatible.
- Secrets, raw frames, and action chunks never appear in default task experience or browser logs.
- Simulation and real adapters share the same manifest, observation, command, recovery, and evidence contracts.
- Physical production remains gated on hardware evidence; CI can certify only simulation and software integration.

### Task 1: Define and validate immutable policy contracts

**Files:**
- Create: `edge/policy/manifest.go`
- Create: `edge/policy/manifest_test.go`
- Create: `edge/policy/types.go`
- Create: `edge/policy/types_test.go`

- [ ] Write failing tests for canonical manifest revision, required identity, supported frameworks, SHA-256 artifact identity, capability/adapter/robot compatibility, observation age, finite action values, action count, action bounds, and command/manifest echo.
- [ ] Implement `Manifest`, `ObservationBundle`, `InferenceRequest`, `InferenceResult`, `Action`, `Decision`, and typed compatibility/validation errors.
- [ ] Run `go test ./edge/policy -count=1` and commit.

### Task 2: Add HTTP policy provider and deterministic acceptance provider

**Files:**
- Create: `edge/policy/provider.go`
- Create: `edge/policy/http.go`
- Create: `edge/policy/http_test.go`
- Create: `edge/policy/deterministic.go`
- Create: `edge/policy/deterministic_test.go`

- [ ] Write failing tests for discovery, bounded response body, timeouts, unavailable sidecar, manifest drift, request identity, deterministic pick/place decisions, and redacted errors.
- [ ] Implement a `Provider` interface, production HTTP provider, and explicit deterministic simulation provider.
- [ ] Run `go test ./edge/policy -count=1` and commit.

### Task 3: Build policy observations from Robot Runtime telemetry

**Files:**
- Modify: `edge/worker/observation.go`
- Create: `edge/worker/policy_observation_test.go`
- Modify: `edge/worker/worker.go`

- [ ] Write failing tests proving entity/robot state, observation time, transform revision, task/command/revision/world/fencing basis, anomalies, and freshness gates enter the policy bundle.
- [ ] Add a pluggable `PolicyObservationProvider`; default to local runtime telemetry and leave immutable frame-reference extension points.
- [ ] Ensure required degraded/missing observations fail before policy inference and before runtime invocation.
- [ ] Run `go test ./edge/worker -count=1` and commit.

### Task 4: Enrich learned physical tools before invocation

**Files:**
- Modify: `edge/worker/worker.go`
- Modify: `cmd/edge-worker/main.go`
- Create: `edge/worker/policy_test.go`
- Modify: `edge/runtime/runtime.go`

- [ ] Write failing tests that capabilities advertising `action_chunk` invoke the policy provider, read-only/verification tools do not, validated actions reach runtime parameters, policy metadata is recorded, mismatches fail closed, and no invocation occurs after rejection.
- [ ] Configure `EDGE_POLICY_MODE=disabled|deterministic|http`, endpoint, timeout, and manifest expectations; default disabled for adapters that do not need learned actions.
- [ ] Add structured policy execution metadata to tool activity while excluding raw actions.
- [ ] Run worker and command tests and commit.

### Task 5: Add structured recovery classification and projection

**Files:**
- Create: `edge/recovery/classifier.go`
- Create: `edge/recovery/classifier_test.go`
- Modify: `edge/worker/worker.go`
- Modify: `tasks/experience_events.go`
- Modify: `tasks/experience.go`
- Modify: `fleet/revisions.go`
- Create: `tasks/policy_recovery_test.go`

- [ ] Write failing table tests for observation wait, policy retry, policy blocked, execution reconcile, safe recovery, and safety stop.
- [ ] Emit idempotent `RECOVERY_ACTIVITY` events with safe public text, attempt count, automatic action, and collapsed professional codes.
- [ ] Retry only pre-side-effect inference failures with bounded backoff; do not replay unknown physical outcomes.
- [ ] Project recovery timeline and policy stage into `task.experience.v1` without raw errors/actions/secrets.
- [ ] Run `go test ./edge/recovery ./edge/worker ./tasks ./fleet -count=1` and commit.

### Task 6: Render policy and recovery progress for non-technical users

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`
- Modify: `web/task_experience_test.mjs`

- [ ] Write failing Node tests for the five-stage learned-tool progress, plain Chinese recovery messages, non-color status cues, collapsed policy evidence, version-gap resync, and action/secret non-disclosure.
- [ ] Extend the mission tool cards and recovery panel without reducing the live WebGL viewport.
- [ ] Preserve text-only DOM rendering, keyboard focus, reduced motion, desktop/mobile layouts, mouse wheel zoom, left-drag pan, and right-drag orbit.
- [ ] Run `make test-web` and commit.

### Task 7: Provide a Python policy-sidecar reference bridge

**Files:**
- Create: `policy/sidecar/tangying_policy_sidecar/__init__.py`
- Create: `policy/sidecar/tangying_policy_sidecar/contracts.py`
- Create: `policy/sidecar/tangying_policy_sidecar/providers.py`
- Create: `policy/sidecar/tangying_policy_sidecar/server.py`
- Create: `policy/sidecar/tests/test_contracts.py`
- Create: `policy/sidecar/tests/test_server.py`
- Modify: `pyproject.toml`

- [ ] Write failing tests for deterministic, callable VLA/IL/RL adapters, manifest validation, request identity, timeout-safe error mapping, finite bounded actions, and health/discovery routes.
- [ ] Implement a dependency-light reference server and callable provider SPI; ML frameworks remain optional provider packages.
- [ ] Document how a LeRobot/VLA policy maps observations to named XLeRobot joint actions without importing hardware SDKs.
- [ ] Run the policy-sidecar pytest subset and commit.

### Task 8: Extend dual-robot and fault acceptance

**Files:**
- Create: `tests/e2e/test_policy_handoff.py`
- Create: `tests/e2e/test_policy_faults.py`
- Modify: `scripts/run_fleet_harness.py`
- Modify: `scripts/run_robocasa_harness.py`
- Modify: `Makefile`

- [ ] Write failing acceptance tests for natural-language decomposition, two policy-backed pick/place tools per robot, 14 tool confirmations, custody fencing, post-command evidence, and terminal success.
- [ ] Add faults for stale observation, camera/source loss, policy timeout/unavailable, manifest/model/adapter/calibration mismatch, malformed/oversized/non-finite action, worker/coordinator restart, unknown execution, and browser reconnect.
- [ ] Require each recoverable fault to emit a recovery event and reach either `SUCCEEDED` or an explicit safe terminal state.
- [ ] Produce machine-readable acceptance artifacts with policy, observation, recovery, Harness, visual-world, and release-gate evidence.
- [ ] Run the deterministic Fleet matrix and RoboCasa retained-evidence revalidation and commit.

### Task 9: Complete production delivery documentation

**Files:**
- Modify: `docs/production/architecture.md`
- Modify: `docs/production/quickstart.md`
- Modify: `docs/production/api-reference.md`
- Modify: `docs/production/data-contracts.md`
- Modify: `docs/production/sim-to-real.md`
- Modify: `docs/production/operations-and-failures.md`
- Modify: `docs/production/testing-and-acceptance.md`
- Modify: `docs/production/release-evidence.md`
- Modify: `docs/production/configuration-and-security.md`
- Modify: `docs/production/README.md`
- Modify: `tests/docs/test_production_docs.py`

- [ ] Document the complete architecture, zero-to-simulation flow, cloud/local/user/simulator/real-robot operation, every new contract and endpoint, provider implementation examples, model promotion, map/calibration, fault diagnosis, and release gates.
- [ ] Add route/config/link/command checks so instructions remain executable.
- [ ] Explicitly separate `SIMULATION_GO`, `SHADOW_GO`, and `PHYSICAL_GO` evidence.
- [ ] Run documentation tests and commit.

### Task 10: Final verification, review, release, and PR

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `docs/production/release-evidence.md`

- [ ] Run focused Go/Python/Node tests after every task.
- [ ] Run `make lint`, `make test`, `make install-check`, deterministic simulation acceptance, fault acceptance, and RoboCasa retained-evidence revalidation from a clean checkout.
- [ ] Capture browser visual evidence from the live HTTP console, including models, task decomposition, policy stages, injected recovery, and final Harness confirmation.
- [ ] Review the diff for secret leakage, backward compatibility, unsafe retry, policy authority creep, and unsupported production claims.
- [ ] Bump the release candidate, update changelog/evidence, push `codex/production-sim2real`, open a PR to `codex/v0.1`, wait for all required checks, fix failures, and merge only when green.
