# XLeRobot Observable Simulation and Semantic Tool Training Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start the complete local simulation stack with one command, immediately display an articulated pinned XLeRobot task scene, execute the two-goal cup-and-bottle request, and train/evaluate a dependency-light semantic tool policy.

**Architecture:** Keep the Agent dependent only on the existing Robot Runtime contract. Extend the MuJoCo runtime with an official pinned XLeRobot model, focused tool/controller/rendering modules, and richer observations; add a Local Agent observer and frame cache; add an independent NumPy semantic RL package that reuses the same tool registry; replace the transient simulation lifecycle mapping with an exact-PID stack supervisor.

**Tech Stack:** Go 1.24, Python 3.11+, MuJoCo 3.x, NumPy, gRPC/protobuf, embedded HTML/CSS/JavaScript, Bash, pytest, Go testing.

**Spec:** `docs/superpowers/specs/2026-08-19-xlerobot-observable-sim-training-design.md`

---

## File Structure

- `sim/mujoco/assets/xlerobot/`: pinned upstream MJCF, referenced mesh files, license, and provenance.
- `sim/mujoco/assets/xlerobot_tabletop.xml`: combines the official robot with the task table, objects, bins, tray, lights, and overview camera.
- `sim/mujoco/tangying_sim/model.py`: model paths, required-name validation, and model revision constants.
- `sim/mujoco/tangying_sim/motion.py`: bounded joint interpolation and home/manipulation poses.
- `sim/mujoco/tangying_sim/tools.py`: semantic tool protocol, context, registry, and one implementation per capability.
- `sim/mujoco/tangying_sim/rendering.py`: MuJoCo overview rendering and standard-library PNG encoding.
- `sim/mujoco/tangying_sim/world.py`: authoritative world state, reset, entity mapping, attachment, and verification.
- `sim/mujoco/tangying_sim/server.py`: gRPC mapping and tool dispatch only.
- `sim/mujoco/tangying_sim/training/env.py`: goal-conditioned discrete semantic tool environment.
- `sim/mujoco/tangying_sim/training/qlearning.py`: Q-learning, atomic versioned checkpoint, load, and evaluation.
- `sim/mujoco/tangying_sim/training/cli.py`: train/evaluate command line entrypoint.
- `core/telemetry/telemetry.go`: optional non-JSON frame bytes and media type.
- `cmd/local-agent/observer.go`: cancellable startup/periodic Runtime observer.
- `console/server.go`: latest scene-frame endpoint.
- `web/index.html`, `web/app.js`, `web/styles.css`: frame, semantic fallback, robot pose, and execution/training state.
- `scripts/sim-stack.sh`: long-running start/stop/restart/status/logs supervisor.
- `internal/robotagent/app.go`: map simulation lifecycle commands to `sim-stack.sh`.
- `tests/e2e/test_observable_sequence.py`: live-stack two-goal acceptance.

### Task 1: Import and Validate the Pinned XLeRobot Model

**Files:**
- Create: `sim/mujoco/assets/xlerobot/xlerobot.xml`
- Create: `sim/mujoco/assets/xlerobot/assets/*.stl`
- Create: `sim/mujoco/assets/xlerobot/PROVENANCE.md`
- Create: `sim/mujoco/assets/xlerobot/LICENSE`
- Create: `sim/mujoco/assets/xlerobot_tabletop.xml`
- Create: `sim/mujoco/tangying_sim/model.py`
- Create: `sim/mujoco/tests/test_model.py`

- [ ] **Step 1: Write the failing model contract test**

```python
import mujoco

from tangying_sim.model import MODEL_REVISION, load_task_model, validate_task_model


def test_task_model_contains_pinned_xlerobot_and_task_scene():
    model = load_task_model()
    validate_task_model(model)
    assert MODEL_REVISION == "3d14695e40c9c68229c0aacffca6053c75cd3eb6"
    for name in ("chassis", "Rotation_L", "Rotation_R", "Jaw_L", "Jaw_R"):
        kind = mujoco.mjtObj.mjOBJ_BODY if name == "chassis" else mujoco.mjtObj.mjOBJ_JOINT
        assert mujoco.mj_name2id(model, kind, name) >= 0
    for name in ("red_cup", "blue_bottle", "right_bin", "front_tray"):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name) >= 0
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run: `.venv/bin/pytest sim/mujoco/tests/test_model.py -q`

Expected: FAIL because `tangying_sim.model` does not exist.

- [ ] **Step 3: Import only upstream files referenced by the official MJCF**

Use upstream repository `https://github.com/Vector-Wangel/XLeRobot`, commit `3d14695e40c9c68229c0aacffca6053c75cd3eb6`, and retain the upstream Apache-2.0 license. `PROVENANCE.md` must list the URL, commit, source paths, and state that the model is not a calibrated representation of later two-wheel revisions.

The combined scene includes the robot via `<include file="xlerobot/xlerobot.xml"/>`, places the table in front of the chassis, preserves entity body names, and defines `<camera name="overview" .../>`.

- [ ] **Step 4: Implement strict model loading and validation**

```python
MODEL_REVISION = "3d14695e40c9c68229c0aacffca6053c75cd3eb6"
REQUIRED_BODIES = ("chassis", "Fixed_Jaw", "Fixed_Jaw_2", "red_cup", "blue_bottle", "right_bin", "front_tray")
REQUIRED_JOINTS = ("Rotation_L", "Pitch_L", "Elbow_L", "Jaw_L", "Rotation_R", "Pitch_R", "Elbow_R", "Jaw_R")


def load_task_model() -> mujoco.MjModel:
    return mujoco.MjModel.from_xml_path(str(TASK_MODEL_PATH))


def validate_task_model(model: mujoco.MjModel) -> None:
    for kind, names in ((mujoco.mjtObj.mjOBJ_BODY, REQUIRED_BODIES), (mujoco.mjtObj.mjOBJ_JOINT, REQUIRED_JOINTS)):
        missing = [name for name in names if mujoco.mj_name2id(model, kind, name) < 0]
        if missing:
            raise ValueError(f"XLeRobot task model is missing {missing}")
```

- [ ] **Step 5: Verify the model compiles and tests pass**

Run: `.venv/bin/pytest sim/mujoco/tests/test_model.py -q`

Expected: PASS and no MuJoCo XML warning or missing-asset error.

- [ ] **Step 6: Commit the model slice**

```bash
git add sim/mujoco/assets/xlerobot sim/mujoco/assets/xlerobot_tabletop.xml sim/mujoco/tangying_sim/model.py sim/mujoco/tests/test_model.py
git commit -m "feat: add pinned xlerobot mujoco task scene"
```

### Task 2: Add Articulated Motion, Semantic Tools, and Scene Rendering

**Files:**
- Create: `sim/mujoco/tangying_sim/motion.py`
- Create: `sim/mujoco/tangying_sim/tools.py`
- Create: `sim/mujoco/tangying_sim/rendering.py`
- Modify: `sim/mujoco/tangying_sim/world.py`
- Modify: `sim/mujoco/tangying_sim/server.py`
- Modify: `sim/mujoco/tests/test_world.py`
- Modify: `sim/mujoco/tests/test_server.py`
- Create: `sim/mujoco/tests/test_tools.py`
- Create: `sim/mujoco/tests/test_rendering.py`

- [ ] **Step 1: Write failing motion and observation tests**

```python
def test_pick_moves_arm_and_reports_robot_entity():
    world = TabletopWorld.seeded(7)
    before = world.joint_positions()
    assert world.tools.execute("manipulation.pick", target_ref="red-cup").success
    assert world.joint_positions() != before
    robot = next(entity for entity in world.entities() if entity.entity_id == "xlerobot")
    assert robot.category == "robot"
    assert world.robot_state()["held"] == "red-cup"


def test_overview_frame_is_png():
    frame = SceneRenderer(TabletopWorld.seeded(7)).render()
    assert frame.media_type == "image/png"
    assert frame.data.startswith(b"\x89PNG\r\n\x1a\n")
```

- [ ] **Step 2: Verify the new contracts fail before implementation**

Run: `.venv/bin/pytest sim/mujoco/tests/test_world.py sim/mujoco/tests/test_tools.py sim/mujoco/tests/test_rendering.py -q`

Expected: FAIL on missing `tools`, `joint_positions`, `robot_state`, and renderer.

- [ ] **Step 3: Implement bounded joint interpolation**

```python
class MotionController:
    def move(self, targets: dict[str, float], *, steps: int = 40) -> None:
        if not 1 <= steps <= self.max_steps:
            raise ValueError("motion step count is outside the safety bound")
        starts = {name: self._qpos(name) for name in targets}
        for alpha in np.linspace(0.0, 1.0, steps):
            for name, target in targets.items():
                self._set_qpos(name, self._clamp(name, starts[name] + alpha * (target - starts[name])))
            self._step_once()
```

Define named HOME, PRE_GRASP, LIFT, PLACE, and OPEN/CLOSED jaw targets for both arms. Every target is clamped to `model.jnt_range`.

- [ ] **Step 4: Implement the shared tool registry**

```python
@dataclass
class ToolResult:
    success: bool
    code: str = "OK"
    message: str = ""
    confidence: float = 1.0


class Tool(Protocol):
    name: str
    def execute(self, context: ToolContext) -> ToolResult: ...


class ToolRegistry:
    def execute(self, name: str, *, target_ref: str = "", parameters: dict | None = None) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, "SKILL_NOT_ALLOWED", name, 0.0)
        return tool.execute(ToolContext(self.world, target_ref, parameters or {}))
```

Server dispatch must call this registry for all semantic skills. Pick/place animate the arm and held object; verify tools read world state and return measured confidence; recovery returns both arms home.

- [ ] **Step 5: Implement PNG rendering without a new runtime dependency**

Use `mujoco.Renderer`, camera `overview`, `zlib.compress`, and PNG IHDR/IDAT/IEND chunks. Renderer exceptions return no frame and append a rendering anomaly; they never change tool results.

- [ ] **Step 6: Populate the full observation contract**

`world.entities()` adds `xlerobot`, `table`, and `floor`. `world.robot_state()` includes `model_revision`, `base_pose`, `joint_positions`, `grippers`, `held`, `active_tool`, `target`, `end_effectors`, `reward`, `episode`, and `verification_confidence`. `RobotRuntimeService._observation()` copies these values and sets `compressed_image` and `image_media_type` when rendering succeeds.

- [ ] **Step 7: Run focused and existing simulation tests**

Run: `.venv/bin/pytest sim/mujoco/tests tests/contract/test_mujoco_gateway.py -q`

Expected: PASS with existing entity IDs and command semantics preserved.

- [ ] **Step 8: Commit the simulation tool slice**

```bash
git add sim/mujoco/tangying_sim sim/mujoco/tests
git commit -m "feat: execute observable articulated simulation tools"
```

### Task 3: Publish Startup Telemetry and Serve Scene Frames

**Files:**
- Modify: `core/telemetry/telemetry.go`
- Modify: `edge/robotclient/client.go`
- Create: `cmd/local-agent/observer.go`
- Create: `cmd/local-agent/observer_test.go`
- Modify: `cmd/local-agent/main.go`
- Modify: `console/server.go`
- Modify: `console/server_test.go`
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`

- [ ] **Step 1: Write failing observer and frame endpoint tests**

```go
func TestObserverPublishesImmediatelyAndStopsWithContext(t *testing.T) {
    source := &fakeTelemetrySource{snapshot: telemetry.Snapshot{Adapter: "mujoco"}}
    sink := make(chan telemetry.Snapshot, 1)
    ctx, cancel := context.WithCancel(context.Background())
    done := startTelemetryObserver(ctx, source, 10*time.Millisecond, func(_ context.Context, got telemetry.Snapshot) error { sink <- got; return nil })
    select { case <-sink: case <-time.After(time.Second): t.Fatal("no startup telemetry") }
    cancel()
    select { case <-done: case <-time.After(time.Second): t.Fatal("observer did not stop") }
}


func TestSceneFrameReturnsLatestImage(t *testing.T) {
    service.PublishTelemetry(context.Background(), telemetry.Snapshot{Adapter: "mujoco", Frame: []byte("png"), FrameMediaType: "image/png"})
    response := serveRequest(server, http.MethodGet, "/v1/scene/frame?adapter=mujoco", "")
    if response.Code != http.StatusOK || response.Header().Get("Content-Type") != "image/png" { t.Fatal(response) }
}
```

- [ ] **Step 2: Verify RED**

Run: `go test ./cmd/local-agent ./console -run 'Observer|SceneFrame' -v`

Expected: FAIL on missing observer and frame fields/route.

- [ ] **Step 3: Extend telemetry without embedding frames in JSON**

```go
type Snapshot struct {
    // existing JSON fields
    Frame          []byte `json:"-"`
    FrameMediaType string `json:"-"`
}
```

`observationToTelemetry` copies `CompressedImage` and `ImageMediaType`. TelemetryHub copies frame bytes on publish/read to prevent aliasing.

- [ ] **Step 4: Add the cancellable observer**

The loop calls `source.Telemetry(ctx, "")` immediately and then on a ticker. Source errors are logged at a bounded rate and retried. Sink errors are non-fatal. `main.run()` starts it with the same root context used by the executor and HTTP server.

- [ ] **Step 5: Add `GET /v1/scene/frame`**

Return `404 SCENE_FRAME_UNAVAILABLE` when no frame exists. On success set the exact media type, `Cache-Control: no-store`, and write bytes. Reject unsupported adapter queries by returning no frame rather than falling back to another robot.

- [ ] **Step 6: Render live frame and observed robot pose in the Console**

Add `<img id="scene-frame">` and retain the canvas fallback. Fetch the image with a timestamp query only when telemetry `hasLatest` is true. Draw the robot footprint and heading from the `xlerobot` entity pose, not `(0,0)`. Display held object, active tool, model revision, reward, and verification confidence.

- [ ] **Step 7: Run Go and static-web tests**

Run: `go test ./core/telemetry ./edge/robotclient ./cmd/local-agent ./console -race -v`

Expected: PASS, including observer cancellation and non-fatal source errors.

- [ ] **Step 8: Commit the observability slice**

```bash
git add core/telemetry edge/robotclient cmd/local-agent console web
git commit -m "feat: stream simulation scene to local console"
```

### Task 4: Implement Semantic Tool RL Training and Evaluation

**Files:**
- Create: `sim/mujoco/tangying_sim/training/__init__.py`
- Create: `sim/mujoco/tangying_sim/training/env.py`
- Create: `sim/mujoco/tangying_sim/training/qlearning.py`
- Create: `sim/mujoco/tangying_sim/training/cli.py`
- Create: `sim/mujoco/tests/training/test_env.py`
- Create: `sim/mujoco/tests/training/test_qlearning.py`
- Modify: `pyproject.toml`
- Create: `scripts/train_semantic_policy.py`

- [ ] **Step 1: Write failing environment tests**

```python
def test_correct_tool_sequence_reaches_verified_terminal_reward():
    env = SemanticToolEnv(seed=7)
    observation, info = env.reset(goal=Goal("cup", "red", "storage_bin", "right_side"))
    total = 0.0
    for action in ("observe_scene", "resolve_targets", "plan_grasp", "manipulation.pick", "verify_grasp", "manipulation.place", "verify_placement"):
        observation, reward, terminated, truncated, info = env.step(action)
        total += reward
    assert terminated and not truncated
    assert info["success"] is True
    assert total > 0


def test_wrong_tool_order_is_penalized_without_false_success():
    env = SemanticToolEnv(seed=7)
    env.reset(goal=Goal("bottle", "blue", "delivery_tray", "front_side"))
    _, reward, terminated, _, info = env.step("manipulation.place")
    assert reward < 0 and not terminated and not info["success"]
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest sim/mujoco/tests/training/test_env.py -q`

Expected: FAIL because the training package does not exist.

- [ ] **Step 3: Implement finite state, actions, rewards, and truncation**

```python
ACTIONS = ("observe_scene", "resolve_targets", "plan_grasp", "manipulation.pick", "verify_grasp", "manipulation.place", "verify_placement", "recover_to_safe_pose")


def state_key(self) -> tuple[str, ...]:
    return (self.goal.kind, self.phase, self.object_id or "", self.destination_id or "", self.world.held or "", str(self.recovery_required))
```

The environment invokes `world.tools.execute`, charges `-0.05` per action, rewards correct stage progress, gives the largest reward only on verified placement, penalizes invalid order/failure, and truncates at a fixed maximum step budget.

- [ ] **Step 4: Write failing trainer/checkpoint tests**

```python
def test_qlearning_checkpoint_round_trip_and_seeded_evaluation(tmp_path):
    result = train(episodes=300, seed=11)
    path = tmp_path / "policy.json"
    save_checkpoint(path, result)
    policy = load_checkpoint(path)
    report = evaluate(policy, episodes=30, seed=29)
    assert report.success_rate >= 0.9
    assert policy.tool_catalog_fingerprint == catalog_fingerprint()
```

- [ ] **Step 5: Implement epsilon-greedy Q-learning and atomic JSON artifacts**

Use a seeded `random.Random`, deterministic tie-breaking, standard Q update, epsilon decay, and a temporary file plus `os.replace`. Reject unknown `schemaVersion`, `stateSchemaVersion`, `actionSchemaVersion`, or tool catalog fingerprint.

- [ ] **Step 6: Add train/evaluate CLI**

```bash
.venv/bin/python scripts/train_semantic_policy.py train --episodes 1000 --seed 7 --output artifacts/training/semantic-policy.json
.venv/bin/python scripts/train_semantic_policy.py evaluate --checkpoint artifacts/training/semantic-policy.json --episodes 100 --seed 1007 --min-success-rate 0.90
```

The commands print one JSON summary line and exit non-zero when evaluation is below the threshold.

- [ ] **Step 7: Run training unit and seeded acceptance tests**

Run: `.venv/bin/pytest sim/mujoco/tests/training -q && .venv/bin/python scripts/train_semantic_policy.py train --episodes 1000 --seed 7 --output artifacts/training/semantic-policy.json && .venv/bin/python scripts/train_semantic_policy.py evaluate --checkpoint artifacts/training/semantic-policy.json --episodes 100 --seed 1007 --min-success-rate 0.90`

Expected: tests PASS; evaluator prints `successRate` at least `0.9` and exits 0.

- [ ] **Step 8: Commit the training slice**

```bash
git add sim/mujoco/tangying_sim/training sim/mujoco/tests/training scripts/train_semantic_policy.py pyproject.toml
git commit -m "feat: train semantic manipulation tool policy"
```

### Task 5: Replace the Transient Simulation Lifecycle With an Exact-PID Stack Supervisor

**Files:**
- Create: `scripts/sim-stack.sh`
- Create: `tests/install/test_sim_stack_contract.py`
- Modify: `internal/robotagent/app.go`
- Modify: `internal/robotagent/app_test.go`
- Modify: `Makefile`

- [ ] **Step 1: Write failing CLI dispatch tests**

```go
func TestSimulationLifecycleUsesPersistentStackSupervisor(t *testing.T) {
    app, runner, _ := newTestApp(t, "sim")
    for _, operation := range []string{"start", "stop", "restart", "status"} {
        runner.commands = nil
        if err := app.Run(context.Background(), []string{operation, "sim"}); err != nil { t.Fatal(err) }
        want := []string{filepath.Join(app.RootDir, "scripts", "sim-stack.sh"), operation}
        if !reflect.DeepEqual(runner.commands[0].Args, want) { t.Fatalf("%s: %#v", operation, runner.commands) }
    }
}
```

The Python contract test asserts fixed loopback defaults, explicit PID files, exact command identity validation, absence of broad `pkill`, health checks, and rollback on partial startup.

- [ ] **Step 2: Verify RED**

Run: `go test ./internal/robotagent -run SimulationLifecycle -v && .venv/bin/pytest tests/install/test_sim_stack_contract.py -q`

Expected: FAIL because lifecycle still maps to `demo.sh`/`pkill` and the supervisor is missing.

- [ ] **Step 3: Implement `sim-stack.sh` lifecycle operations**

The script accepts only `start|stop|restart|status|logs`. It resolves the repository root, uses `artifacts/sim-stack/{run,logs,local-agent}`, validates `.venv/bin/python` and binaries, refuses occupied ports, starts both processes with separate logs, records exact child PIDs atomically, waits for Local Agent health and RuntimeInfo adapter `mujoco`, and rolls back its own children on failure.

Stop verifies `/proc` or `ps` command identity before signalling each recorded PID, waits up to five seconds, and sends KILL only to the still-running recorded child. Status checks both PIDs and both endpoints. Logs supports an optional `--follow` argument.

- [ ] **Step 4: Map stable CLI lifecycle commands to the supervisor**

```go
case "sim":
    script := filepath.Join(a.RootDir, "scripts", "sim-stack.sh")
    if operation == "logs" {
        args := []string{script, "logs"}
        if follow { args = append(args, "--follow") }
        return "bash", args, nil
    }
    return "bash", []string{script, operation}, nil
```

- [ ] **Step 5: Run lifecycle tests and a real start/status/stop smoke test**

Run: `go test ./internal/robotagent -v && .venv/bin/pytest tests/install/test_sim_stack_contract.py -q && bash scripts/sim-stack.sh start && bash scripts/sim-stack.sh status && bash scripts/sim-stack.sh stop`

Expected: all tests PASS; start prints Console URL; status reports both services healthy; stop removes PID files and leaves no recorded child running.

- [ ] **Step 6: Commit the lifecycle slice**

```bash
git add scripts/sim-stack.sh tests/install/test_sim_stack_contract.py internal/robotagent Makefile
git commit -m "feat: add persistent simulation stack lifecycle"
```

### Task 6: Run the Complete Observable Two-Goal Flow and Update Documentation

**Files:**
- Create: `tests/e2e/test_observable_sequence.py`
- Modify: `tests/e2e/helpers.py`
- Modify: `scripts/run_simulation_acceptance.py`
- Modify: `README.md`
- Modify: `docs/quickstart.md`
- Modify: `docs/user-console.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Write the failing live-stack acceptance test**

```python
def test_live_stack_observes_scene_before_approval_and_completes_two_goals(sim_stack):
    initial = sim_stack.get_json("/v1/telemetry?adapter=mujoco&limit=1")
    assert initial["hasLatest"] is True
    ids = {entity["entityId"] for entity in initial["latest"]["entities"]}
    assert {"xlerobot", "red-cup", "blue-bottle", "right-bin", "front-tray"} <= ids
    task = sim_stack.run_task("把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来")
    assert task["state"] == "SUCCEEDED"
    final = sim_stack.get_json("/v1/telemetry?adapter=mujoco&limit=1")["latest"]
    assert final["robotState"]["held"] == ""
    assert final["robotState"]["placements"]["red-cup"] == "right-bin"
    assert final["robotState"]["placements"]["blue-bottle"] == "front-tray"
```

- [ ] **Step 2: Verify the acceptance test fails on the old behavior**

Run: `.venv/bin/pytest tests/e2e/test_observable_sequence.py -q`

Expected: FAIL if startup telemetry, robot entity, or final placement evidence is missing.

- [ ] **Step 3: Add final placement evidence and preserve the object matrix**

Expose a copy of `placements` in robot state and extend `run_simulation_acceptance.py` with the exact two-goal sequence. Do not weaken the existing 18-goal object/destination matrix or safety violation threshold.

- [ ] **Step 4: Document the supported command and fidelity boundary**

README and quickstart lead with `./bin/robot-agent start sim`, the Console URL, status/log/stop commands, training/evaluation commands, expected task result, official model revision, and the statement that this is not a calibrated latest two-wheel digital twin. Architecture and Console docs show the observer/frame cache and shared tool-training boundary.

- [ ] **Step 5: Run the focused full-flow verification**

Run: `make build && .venv/bin/pytest tests/e2e/test_observable_sequence.py tests/e2e/test_sequence.py tests/e2e/test_pick_place.py tests/e2e/test_fetch.py -q && .venv/bin/python scripts/run_simulation_acceptance.py --episodes 30 --seed 20260819`

Expected: build exits 0; all e2e tests PASS; acceptance reports 30 successful episodes, zero safety violations, and all object matrix goals successful.

- [ ] **Step 6: Run the complete repository verification**

Run: `go test ./... -race && .venv/bin/pytest -q && .venv/bin/ruff check . && make generate-check && make build`

Expected: every command exits 0 with zero failures.

- [ ] **Step 7: Verify the real user workflow from a stopped state**

Run:

```bash
./bin/robot-agent start sim
./bin/robot-agent status sim
curl -fsS 'http://127.0.0.1:8787/v1/telemetry?adapter=mujoco&limit=1'
curl -fsS -o artifacts/sim-stack/scene.png 'http://127.0.0.1:8787/v1/scene/frame?adapter=mujoco'
./bin/robot-agent stop sim
```

Expected: start/status succeed; telemetry contains the robot and task entities before approval; scene image begins with a PNG signature; stop succeeds with no stack child remaining.

- [ ] **Step 8: Commit the acceptance and documentation slice**

```bash
git add tests/e2e scripts/run_simulation_acceptance.py README.md docs/quickstart.md docs/user-console.md docs/architecture.md
git commit -m "test: verify observable xlerobot simulation flow"
```

### Task 7: Final Requirement Audit

**Files:**
- Modify: `docs/superpowers/plans/2026-08-19-xlerobot-observable-sim-training.md`

- [ ] **Step 1: Re-read the design acceptance criteria and record evidence**

Append a `Delivered Evidence` section containing the exact commands, exit codes, test counts, policy evaluation success rate, live task ID/state, and paths to the scene image, checkpoint, acceptance JSON, and stack logs.

- [ ] **Step 2: Check the final diff for unrelated or generated-secret content**

Run: `git status --short && git diff --check HEAD~6..HEAD && git diff --stat HEAD~6..HEAD`

Expected: only this feature, its tests, imported attributed model assets, and previously existing user changes are present; no API key, local environment file, database, PID file, or log is staged.

- [ ] **Step 3: Commit delivery evidence**

```bash
git add docs/superpowers/plans/2026-08-19-xlerobot-observable-sim-training.md
git commit -m "docs: record observable simulation verification"
```
