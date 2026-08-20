# RoboCasa Dual XLeRobot Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install RoboCasa and run a Chinese natural-language two-XLeRobot handoff through Fleet, an explicit Harness evaluator, concrete Robot Runtime tools, one shared RoboCasa/MuJoCo world, and the live Console.

**Architecture:** RoboCasa runs in a dedicated Conda environment and supplies a deterministic `KitchenArena`. A pure MJCF composer adds two prefixed copies of the existing XLeRobot to one model; two gRPC Runtime views share one locked simulation state. Fleet continues to own tasks, leases and fencing, while a new Harness package decides completion from post-command `WorldSnapshot` evidence.

**Tech Stack:** Go 1.24, Python 3.11, Conda, RoboCasa 1.0.1, robosuite master, MuJoCo 3.3.1, gRPC, pytest, Node test runner, browser acceptance.

**Spec:** `docs/superpowers/specs/2026-08-21-robocasa-dual-xlerobot-harness-design.md`

## Global Constraints

- Do not modify tracked source inside `datasets/robocasa` or `datasets/robosuite`.
- Use Conda environment `tangying-robocasa`; do not add RoboCasa dependencies to the main `.venv`.
- Fixed scene identity is `robocasa-handoff-v1`, layout 1, style 1, seed 7.
- Both robots and `red-block` must exist in one `mujoco.MjModel/MjData`.
- Tool success never directly proves physical completion; Harness requires post-command world evidence.
- Preserve the existing MuJoCo, Local Brain and Fleet adapters.
- All new behavior is developed test-first and every task ends with its focused verification.

---

### Task 1: Idempotent RoboCasa installation and smoke contract

**Files:**
- Create: `scripts/setup-robocasa.sh`
- Create: `scripts/robocasa-smoke.py`
- Create: `tests/install/test_robocasa_setup.py`
- Modify: `.gitignore`
- Modify: `Makefile`

**Interfaces:**
- Produces: Conda environment `tangying-robocasa`, editable `robosuite`, `robocasa`, and AgentOS packages.
- Produces: `make robocasa-install` and `make robocasa-smoke`.
- Keeps: downloaded assets inside the upstream clone where its `.gitignore` already excludes them.

- [ ] **Step 1: Write failing installer contract tests**

```python
def test_setup_script_uses_isolated_conda_and_official_clones():
    script = Path("scripts/setup-robocasa.sh").read_text()
    assert 'ROBOCASA_ENV_NAME="${ROBOCASA_ENV_NAME:-tangying-robocasa}"' in script
    assert "ARISE-Initiative/robosuite.git" in script
    assert "pip install -e" in script
    assert "download_kitchen_assets" in script


def test_smoke_script_loads_fixed_kitchen_arena():
    script = Path("scripts/robocasa-smoke.py").read_text()
    assert "KitchenArena(layout_id=1, style_id=1" in script
    assert "mujoco.MjModel.from_xml_string" in script
```

- [ ] **Step 2: Run the test and verify missing scripts fail**

Run: `.venv/bin/pytest -q tests/install/test_robocasa_setup.py`

Expected: FAIL because `scripts/setup-robocasa.sh` does not exist.

- [ ] **Step 3: Implement the installer and smoke test**

The installer must:

```bash
set -euo pipefail
ROBOCASA_ENV_NAME="${ROBOCASA_ENV_NAME:-tangying-robocasa}"
conda create -y -n "$ROBOCASA_ENV_NAME" python=3.11
git clone https://github.com/ARISE-Initiative/robosuite.git datasets/robosuite
conda run -n "$ROBOCASA_ENV_NAME" python -m pip install -e datasets/robosuite
conda run -n "$ROBOCASA_ENV_NAME" python -m pip install -e datasets/robocasa
conda run -n "$ROBOCASA_ENV_NAME" python -m pip install -e .
```

Each create/clone/install/download step must first check its completion marker. The smoke script constructs `KitchenArena(layout_id=1, style_id=1, rng=np.random.default_rng(7), clutter_mode=0)`, compiles its XML headlessly and prints JSON versions plus fixture count.

- [ ] **Step 4: Run static contract tests**

Run: `.venv/bin/pytest -q tests/install/test_robocasa_setup.py`

Expected: PASS.

- [ ] **Step 5: Install official dependencies and assets**

Run: `make robocasa-install`

Expected: Conda environment exists, all six official asset groups download, and nested repositories remain clean except upstream-ignored generated files.

- [ ] **Step 6: Run the real smoke test**

Run: `make robocasa-smoke`

Expected: JSON reports `robocasa=1.0.1`, MuJoCo model dimensions and a positive fixture count.

- [ ] **Step 7: Commit**

```bash
git add .gitignore Makefile scripts/setup-robocasa.sh scripts/robocasa-smoke.py tests/install/test_robocasa_setup.py
git commit -m "build: install isolated RoboCasa environment"
```

### Task 2: Deterministic MJCF prefixing and composition

**Files:**
- Create: `sim/robocasa/tangying_robocasa/__init__.py`
- Create: `sim/robocasa/tangying_robocasa/composer.py`
- Create: `sim/robocasa/tests/test_composer.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `prefix_mjcf(root: Element, prefix: str, source_dir: Path) -> Element`.
- Produces: `compose_handoff_scene(config: SceneConfig) -> ComposedScene` where `ComposedScene.xml`, `model_hash`, and `names` are immutable.
- Consumes: RoboCasa `KitchenArena` XML and `sim/mujoco/assets/xlerobot/xlerobot.xml`.

- [ ] **Step 1: Write failing pure XML tests**

```python
def test_prefix_mjcf_rewrites_names_references_classes_and_files(tmp_path):
    root = ET.fromstring(XML_WITH_BODY_JOINT_MESH_DEFAULT_AND_ACTUATOR)
    result = prefix_mjcf(root, "robot-1__", tmp_path)
    assert result.find(".//body").get("name") == "robot-1__chassis"
    assert result.find(".//motor").get("joint") == "robot-1__wheel"
    assert result.find(".//geom").get("mesh") == "robot-1__base_mesh"
    assert Path(result.find(".//mesh").get("file")).is_absolute()


def test_two_prefixed_robots_have_no_duplicate_names():
    scene = compose_fixture_scene_for_test()
    assert len(scene.names) == len(set(scene.names))
    mujoco.MjModel.from_xml_string(scene.xml)
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `.venv/bin/pytest -q sim/robocasa/tests/test_composer.py`

Expected: FAIL with missing module/function.

- [ ] **Step 3: Implement prefixing**

Rewrite `name`, named default `class`, `childclass`, and reference attributes `joint`, `joint1`, `joint2`, `body`, `body1`, `body2`, `geom`, `geom1`, `geom2`, `site`, `site1`, `site2`, `tendon`, `actuator`, `mesh`, `material`, `texture`, `hfield`, `camera`, `target`, and `objname`. Resolve mesh/texture file paths against the source compiler directories before merging.

- [ ] **Step 4: Implement scene composition**

Merge top-level `asset`, `default`, `worldbody`, `contact`, `equality`, `tendon`, `sensor`, and `actuator` sections. Add `red-block`, `left-start-zone`, `handoff-zone`, `right-target-zone`, `overview`, `robot-1-evidence`, and `robot-2-evidence`. Reject duplicate names and unresolved references before returning the SHA-256 model hash.

- [ ] **Step 5: Run pure and real composition tests**

Run: `.venv/bin/pytest -q sim/robocasa/tests/test_composer.py`

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_composer.py -m robocasa`

Expected: two XLeRobot chassis, two complete actuator sets and one `red-block` compile in one model.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml sim/robocasa
git commit -m "feat: compose two XLeRobots into RoboCasa world"
```

### Task 3: Shared RoboCasa world and robot-specific views

**Files:**
- Create: `sim/robocasa/tangying_robocasa/config.py`
- Create: `sim/robocasa/tangying_robocasa/world.py`
- Create: `sim/robocasa/tests/test_world.py`

**Interfaces:**
- Produces: `RoboCasaSharedWorld.from_scene(scene: ComposedScene, seed: int) -> RoboCasaSharedWorld`.
- Produces: `RoboCasaRobotView(shared, robot_id)` implementing the methods consumed by `tangying_sim.server.RobotRuntimeService`.
- Guarantees: both views expose one object identity and synchronize mutations under one reentrant lock.

- [ ] **Step 1: Write failing shared-object tests**

```python
def test_sender_place_becomes_receiver_observation(shared_world):
    sender = RoboCasaRobotView(shared_world, "robot-1")
    receiver = RoboCasaRobotView(shared_world, "robot-2")
    assert sender.pick("red-block").success
    assert sender.place("handoff-zone").success
    entity = entity_by_id(receiver.entities(), "red-block")
    assert entity.relations["inside"] == "handoff-zone"


def test_only_current_custodian_can_pick(shared_world):
    receiver = RoboCasaRobotView(shared_world, "robot-2")
    result = receiver.pick("red-block")
    assert not result.success
    assert result.code == "RESOURCE_NOT_OWNED"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_world.py`

Expected: FAIL because world/view classes do not exist.

- [ ] **Step 3: Implement world state and semantic observations**

Track `episode_id`, model hash, sim time, owner, custodian, fencing token, held object, placements and per-source sequences. Robot/entity observations must use `frame_id="world"` and `transform_revision="robocasa-world-v1"`.

- [ ] **Step 4: Implement deterministic motion and attachment**

Move the selected arm through home, pre-grasp, grasp, transfer and release keyframes while stepping MuJoCo. Attach only inside the grasp window; release only inside the destination window; after release step until velocity and position are stable for two observations.

- [ ] **Step 5: Implement RGB and occupancy observations**

Render `overview` and both evidence cameras. Rasterize fixtures, robots, zones and entities in the same world frame; emit grid origin and `cell_size_m=0.1`.

- [ ] **Step 6: Run world tests**

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_world.py`

Expected: shared identity, ownership, placement, rendering and world-frame grid tests pass.

- [ ] **Step 7: Commit**

```bash
git add sim/robocasa/tangying_robocasa sim/robocasa/tests/test_world.py
git commit -m "feat: add shared RoboCasa handoff world"
```

### Task 4: Two Robot Runtime endpoints over one simulation

**Files:**
- Create: `sim/robocasa/tangying_robocasa/fleet_server.py`
- Create: `sim/robocasa/tests/test_runtime.py`
- Modify: `sim/mujoco/tangying_sim/server.py`

**Interfaces:**
- Produces CLI: `python -m tangying_robocasa.fleet_server --sender-listen HOST:PORT --receiver-listen HOST:PORT --seed 7`.
- Reuses: `RobotRuntimeService` validation and concrete tools.
- Produces: separate immutable tool and observation catalogs for `robot-1` and `robot-2` with adapter `robocasa`.

- [ ] **Step 1: Write failing runtime contract tests**

```python
def test_runtime_info_identifies_robocasa_and_robot(runtime_pair):
    sender, receiver = runtime_pair
    assert sender.GetRuntimeInfo(empty).robot_id == "robot-1"
    assert receiver.GetRuntimeInfo(empty).robot_id == "robot-2"
    assert sender.GetRuntimeInfo(empty).adapter == "robocasa"


def test_duplicate_command_is_effectively_once(runtime_pair):
    first = list(runtime_pair.sender.Execute(command("manipulation.pick")))
    second = list(runtime_pair.sender.Execute(command("manipulation.pick")))
    assert terminal(first) == terminal(second)
    assert runtime_pair.world.pick_count("robot-1") == 1
```

- [ ] **Step 2: Run tests and verify failure**

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_runtime.py`

Expected: FAIL with missing fleet server.

- [ ] **Step 3: Generalize the existing Runtime service adapter label**

Add constructor parameters `adapter` and catalog provider without changing MuJoCo defaults. Preserve schema, deadline, idempotency, cancel, estop and fencing validation.

- [ ] **Step 4: Implement shared-process dual gRPC server**

Create one shared world, two robot views and two gRPC servers. A signal handler stops both servers and checkpoints the shared world exactly once.

- [ ] **Step 5: Run runtime tests and old MuJoCo tests**

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_runtime.py`

Run: `.venv/bin/pytest -q sim/mujoco/tests`

Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add sim/robocasa sim/mujoco/tangying_sim/server.py
git commit -m "feat: expose dual RoboCasa robot runtimes"
```

### Task 5: Explicit Harness Agent evidence evaluator

**Files:**
- Create: `core/harness/evaluator.go`
- Create: `core/harness/evaluator_test.go`
- Modify: `fleet/coordinator/coordinator.go`
- Modify: `fleet/coordinator/coordinator_test.go`

**Interfaces:**
- Produces: `func (a *Agent) Evaluate(input Input) Verdict`.
- `Input` contains `Intent`, `EvidenceBasis`, current `worldmodel.Snapshot`, entity ID, destination ID, robot ID and expected resource state.
- `Verdict.Status` is `WAITING`, `SATISFIED`, `RETRYABLE_FAILURE`, or `FAILED_SAFE`; `EvidenceIDs` records the observations used.

- [ ] **Step 1: Write failing evaluator tests**

```go
func TestRejectsToolSuccessWithoutPostCommandWorldEvidence(t *testing.T) {
    verdict := newAgent().Evaluate(inputWithOnlyPreCommandEvidence())
    require.Equal(t, harness.Waiting, verdict.Status)
    require.Equal(t, "WORLD_REVISION_NOT_ADVANCED", verdict.Reason)
}

func TestAcceptsStableFreshPostCommandPlacement(t *testing.T) {
    verdict := newAgent().Evaluate(inputWithTwoFreshPlacementObservations())
    require.Equal(t, harness.Satisfied, verdict.Status)
    require.Len(t, verdict.EvidenceIDs, 2)
}
```

- [ ] **Step 2: Run and verify failure**

Run: `go test ./core/harness ./fleet/coordinator`

Expected: FAIL because `core/harness` does not exist.

- [ ] **Step 3: Implement the pure evaluator**

Evaluate revision, observed time, source sequence, observation count, stable count, freshness, robot emergency state, relation, owner and fencing token in a deterministic order. Return stable reason codes and no side effects.

- [ ] **Step 4: Delegate coordinator completion to Harness**

Replace local post-claim evidence branching with `harness.Agent.Evaluate`. Persist the verdict reason and observation IDs in the completion domain event; publish terminal events only for `SATISFIED`.

- [ ] **Step 5: Run coordinator tests and race detector**

Run: `go test -race ./core/harness ./fleet/coordinator ./fleet/eventlog`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/harness fleet/coordinator
git commit -m "feat: gate physical completion through Harness Agent"
```

### Task 6: Fleet/Edge RoboCasa deployment profile

**Files:**
- Create: `scripts/robocasa-fleet.sh`
- Create: `tests/install/test_robocasa_fleet_script.py`
- Modify: `cmd/edge-worker/main.go`
- Modify: `edge/worker/worker.go`
- Modify: `README.md`
- Modify: `Makefile`

**Interfaces:**
- Produces: `make robocasa-fleet` for an interactive local cloud stack.
- Edge advertises adapter `robocasa`, world ID `robocasa-handoff-v1`, frame `world`, transform `robocasa-world-v1`.
- Fleet receives the same Robot Runtime protobuf and HTTP telemetry schema as the MuJoCo profile.

- [ ] **Step 1: Write failing deployment profile tests**

```python
def test_robocasa_fleet_launches_one_sim_and_two_edges():
    script = Path("scripts/robocasa-fleet.sh").read_text()
    assert script.count("edge-worker") >= 2
    assert "tangying_robocasa.fleet_server" in script
    assert "EDGE_ADAPTER=robocasa" in script
    assert "robocasa-world-v1" in script
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/pytest -q tests/install/test_robocasa_fleet_script.py`

Expected: FAIL because launch script is missing.

- [ ] **Step 3: Implement profile and adapter propagation**

The script creates temporary certificates, starts Fleet, starts one RoboCasa process, starts two Edge Workers, prints the Console URL and traps termination to stop only its child processes.

- [ ] **Step 4: Run focused Go/static tests**

Run: `go test ./cmd/edge-worker ./edge/worker ./fleet/...`

Run: `.venv/bin/pytest -q tests/install/test_robocasa_fleet_script.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Makefile README.md scripts/robocasa-fleet.sh tests/install/test_robocasa_fleet_script.py cmd/edge-worker edge/worker
git commit -m "feat: add RoboCasa Fleet deployment profile"
```

### Task 7: Real multi-process RoboCasa handoff Harness

**Files:**
- Create: `tests/e2e/robocasa_harness.py`
- Create: `tests/e2e/test_robocasa_handoff.py`
- Create: `scripts/run_robocasa_harness.py`
- Modify: `Makefile`

**Interfaces:**
- Produces: `start_robocasa_handoff_stack(tmp_path) -> FleetHandoffStack`.
- Produces: `make test-robocasa-e2e` and evidence under `artifacts/robocasa-harness/manual/`.
- Uses only public HTTP, mTLS Fleet Link and gRPC Runtime process boundaries.

- [ ] **Step 1: Write failing E2E test**

```python
def test_chinese_handoff_is_harness_verified(robocasa_stack):
    task_id = robocasa_stack.create_and_approve(HANDOFF_PROMPT, adapter="robocasa")
    task = robocasa_stack.wait_task(task_id)
    world = robocasa_stack.api("/v1/world")
    verdicts = robocasa_stack.api(f"/v1/tasks/{task_id}/harness-verdicts")
    assert task["state"] == "SUCCEEDED"
    assert world["entities"]["red-block"]["relations"]["inside"] == "right-target-zone"
    assert world["resources"]["block:red-block"]["owner"] == "environment"
    assert [item["status"] for item in verdicts][-2:] == ["SATISFIED", "SATISFIED"]
```

- [ ] **Step 2: Run and verify failure**

Run: `conda run -n tangying-robocasa python -m pytest -q tests/e2e/test_robocasa_handoff.py`

Expected: FAIL because fixture/harness endpoint is missing.

- [ ] **Step 3: Implement process harness and evidence endpoint**

Build Fleet/Edge binaries, start one RoboCasa process and two Edge processes, log every child separately, log in as operator, submit the exact Chinese task and collect task, world, leases, events, intents, Harness verdicts, tool traces and model hash.

- [ ] **Step 4: Run the normal E2E**

Run: `make test-robocasa-e2e`

Expected: both intents succeed, block events occur exactly once, model contains both robots, world revisions increase and final placement is stable.

- [ ] **Step 5: Commit**

```bash
git add Makefile tests/e2e/robocasa_harness.py tests/e2e/test_robocasa_handoff.py scripts/run_robocasa_harness.py
git commit -m "test: add RoboCasa dual robot process harness"
```

### Task 8: Checkpoint recovery and distributed fault matrix

**Files:**
- Create: `sim/robocasa/tangying_robocasa/checkpoint.py`
- Create: `sim/robocasa/tests/test_checkpoint.py`
- Create: `tests/e2e/test_robocasa_faults.py`
- Modify: `tests/e2e/robocasa_harness.py`
- Modify: `scripts/run_robocasa_harness.py`

**Interfaces:**
- Produces: `CheckpointStore.save(world)`, `CheckpointStore.restore(world)` with atomic replace and model-hash validation.
- Fault harness supports pausing/restarting named processes and moving `red-block` through a test-only simulator control channel bound to localhost.

- [ ] **Step 1: Write failing checkpoint tests**

```python
def test_checkpoint_restores_release_without_reexecuting_tool(world, tmp_path):
    execute_sender_handoff(world)
    CheckpointStore(tmp_path / "state.json").save(world)
    restored = new_world()
    CheckpointStore(tmp_path / "state.json").restore(restored)
    assert restored.red_block_relation == "handoff-zone"
    assert restored.tool_result(COMMAND_ID).terminal


def test_checkpoint_rejects_different_model_hash(world, tmp_path):
    store = CheckpointStore(tmp_path / "state.json")
    store.save(world)
    with pytest.raises(ModelHashMismatch):
        store.restore(world_with_other_hash())
```

- [ ] **Step 2: Run and verify failure**

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_checkpoint.py`

Expected: FAIL because checkpoint module is missing.

- [ ] **Step 3: Implement atomic checkpointing**

Serialize episode ID, seed, model hash, sim time, qpos, qvel, owner, custodian, fencing, held object, placements, command results and source sequences. Write to a same-directory temporary file, fsync and `os.replace`.

- [ ] **Step 4: Implement fault cases**

Add tests for duplicate/reordered observation, live Edge `SIGSTOP`, runtime crash before release, runtime crash after release before ACK, stale fencing, receiver offline, camera-only updates, semantic freeze, external block move, unreachable target, collision and estop.

- [ ] **Step 5: Run fault matrix**

Run: `conda run -n tangying-robocasa python -m pytest -q tests/e2e/test_robocasa_faults.py`

Expected: all faults either recover to success or fail closed without double ownership/false completion.

- [ ] **Step 6: Commit**

```bash
git add sim/robocasa/tangying_robocasa/checkpoint.py sim/robocasa/tests/test_checkpoint.py tests/e2e/robocasa_harness.py tests/e2e/test_robocasa_faults.py scripts/run_robocasa_harness.py
git commit -m "test: recover RoboCasa handoff across distributed faults"
```

### Task 9: Console RoboCasa evidence and browser acceptance

**Files:**
- Modify: `web/app.js`
- Modify: `web/app_test.mjs`
- Modify: `web/index.html`
- Modify: `web/styles.css`
- Modify: `scripts/run_robocasa_harness.py`

**Interfaces:**
- Console continues to use `/v1/world`, `/v1/maps/global`, `/v1/scene/frames`, task and Harness endpoints.
- Adds visible scene ID, adapter, model hash and latest Harness verdict without a RoboCasa-specific rendering protocol.

- [ ] **Step 1: Write failing semantic UI tests**

```javascript
test("RoboCasa world shows model identity and Harness verdict", () => {
  const harness = createHarness();
  harness.hooks.renderFleetWorld(robocasaSnapshot);
  assert.match(harness.elements.get("fleet-world-model").textContent, /robocasa-handoff-v1/);
  assert.match(harness.elements.get("fleet-harness-verdict").textContent, /SATISFIED/);
});
```

- [ ] **Step 2: Run and verify failure**

Run: `node --test web/app_test.mjs web/world_view_test.mjs`

Expected: FAIL because identity/verdict elements do not exist.

- [ ] **Step 3: Implement semantic additions**

Display adapter/scene/model identity and Harness evidence separately from transport `LIVE`. Keep source freshness and degraded sources visible. Do not use camera frames to advance semantic status.

- [ ] **Step 4: Run browser unit tests**

Run: `make test-web`

Expected: PASS.

- [ ] **Step 5: Run real browser acceptance**

Start the live RoboCasa harness, log in, submit the Chinese request and verify two online devices, two live frames, `SUCCEEDED`, two satisfied intents, final owner environment, source freshness, global map alignment, left-pan, right-orbit, pointer-anchored zoom, WebSocket resync and zero console/page/network errors. Save `artifacts/robocasa-harness/manual/live-ui.png`.

- [ ] **Step 6: Commit**

```bash
git add web scripts/run_robocasa_harness.py
git commit -m "feat: show RoboCasa Harness evidence in Console"
```

### Task 10: Full regression, evidence pack and real-robot handoff documentation

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Create: `docs/robocasa-harness.md`
- Modify: `docs/distributed-agentos.md`
- Modify: `Makefile`

**Interfaces:**
- Produces: `make robocasa-evidence` and an exact runbook from installation through evidence inspection.
- Documents the simulation-to-real replacement boundary: tools, observations, transform revision, map and safety.

- [ ] **Step 1: Write failing documentation contract tests**

```python
def test_readme_documents_robocasa_vertical_slice():
    readme = Path("README.md").read_text()
    assert "make robocasa-install" in readme
    assert "make robocasa-evidence" in readme
    assert "robocasa-handoff-v1" in readme
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/pytest -q tests/install/test_readme_contract.py`

Expected: FAIL until commands are documented.

- [ ] **Step 3: Complete runbook and evidence target**

Document dependency size, Conda isolation, commands, expected output, fault semantics, current deterministic-grasp limitation, map registration required for real hardware and production blockers. Evidence target writes versions, hashes, task/world/events/verdicts/traces/faults/videos/screenshot and a top-level pass/fail summary.

- [ ] **Step 4: Run focused and full verification**

Run: `make robocasa-evidence`

Run: `make test`

Run: `make lint`

Run: `go test -race ./core/harness ./fleet/coordinator ./fleet/eventlog ./fleet/gateway ./fleet/worldhub ./edge/worker`

Run: `git diff --check`

Expected: all pass; evidence summary has every invariant true.

- [ ] **Step 5: Inspect generated media and process cleanup**

Visually inspect the Console screenshot and both robot videos. Confirm no temporary Fleet, Edge, RoboCasa or browser processes remain.

- [ ] **Step 6: Commit**

```bash
git add README.md CHANGELOG.md docs Makefile tests/install/test_readme_contract.py
git commit -m "docs: document RoboCasa simulation-to-real workflow"
```
