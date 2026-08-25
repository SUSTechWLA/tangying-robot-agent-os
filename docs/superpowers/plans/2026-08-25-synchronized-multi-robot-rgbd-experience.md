# Synchronized Multi-Robot RGB-D Task Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the browser truthfully show a repeatable natural-language two-XLeRobot handoff as ordered subtasks, registered tool calls, synchronized MuJoCo joint/environment motion, head-mounted RGB-D evidence, and Harness-confirmed completion.

**Architecture:** RoboCasa/MuJoCo remains the only simulation truth source. A simulation-only task prelude resets a new episode through registered tools, and each Robot Runtime publishes one immutable capture bundle containing robot state, entities, RGB, depth, and capture identity from the same `MjData` copy. Edge, Fleet, the World Model, task experience, and the browser preserve that identity; the browser renders only monotonic correlated state and uses one bounded session lifecycle for all polling.

**Tech Stack:** Go 1.24, Python 3.11, MuJoCo 3.3.1, RoboCasa 1.0.1, gRPC/protobuf, Redis/MySQL Fleet services, vanilla browser JavaScript, Three.js WebGL, Node test runner, pytest.

**Spec:** `docs/superpowers/specs/2026-08-24-synchronized-multi-robot-rgbd-experience-design.md`

## Global Constraints

- MuJoCo is the only physical truth source in simulation; browser interpolation and tool acknowledgements cannot prove completion.
- The demo request sets `executionContext.mode = "simulation_demo"` and `executionContext.newEpisode = true`; physical adapters never execute the simulation reset prelude.
- Reset command replay is idempotent and external fencing tokens never move backwards.
- RGB, metric depth, robot state, entities, and calibration in one `sensor.capture.v1` bundle come from the same immutable `MjData` copy.
- Every sensor bundle carries `captureId`, `episodeId`, `simulationStep`, `sourceSequence`, `capturedAt`, `frameId`, and `transformRevision`.
- Raw RGB-D bytes do not enter task or domain events; events carry only bounded capture identity, hashes, and world revision metadata.
- `GET /v1/tasks?view=summary&limit=20` omits task event arrays and plans.
- A browser dashboard owns at most one timer per polling function and fetches frame bytes only when a capture ID or ETag changes.
- Existing Local Brain and physical robot create-task requests remain backward compatible.
- Simulation acceptance is `SIMULATION_GO`; it cannot promote a physical release.

---

### Task 1: Persist simulation execution context and prepend a visible episode-preparation intent

**Files:**
- Modify: `tasks/service.go`
- Modify: `tasks/revision.go`
- Modify: `tasks/service_test.go`
- Modify: `middleware/sqlite/store.go`
- Modify: `middleware/sqlite/tasks.go`
- Modify: `middleware/sqlite/tasks_test.go`
- Modify: `fleet/server.go`
- Modify: `fleet/server_test.go`
- Modify: `console/server.go`
- Modify: `console/server_test.go`
- Modify: `skills/manipulation/intent.go`

**Interfaces:**
- Produces: `tasks.ExecutionContext`, `tasks.CreateCommand`, `(*tasks.Service).CreateCommand(context.Context, tasks.CreateCommand)`, and `manipulation.ActionPrepareSimulation`.
- Preserves: `(*tasks.Service).Create(ctx, request, adapter)` as a compatibility wrapper.
- Later tasks consume: `Task.ExecutionContext`, and the first `Intent.Tasks()` entry with action `prepare_simulation` and robot ID `robot-1`.

- [ ] **Step 1: Write failing task-service and HTTP contract tests**

Add these service tests and equivalent Fleet/Console request tests:

```go
func TestCreateCommandPrependsSimulationEpisodePreparation(t *testing.T) {
	service := NewService(NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.CreateCommand(context.Background(), CreateCommand{
		Request: "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块放到右侧目标区",
		Adapter: "robocasa",
		ExecutionContext: ExecutionContext{Mode: "simulation_demo", NewEpisode: true},
	})
	if err != nil { t.Fatal(err) }
	intents := task.Intent.Tasks()
	if len(intents) != 3 || intents[0].Action != manipulation.ActionPrepareSimulation || intents[0].RobotID != "robot-1" {
		t.Fatalf("intents=%#v", intents)
	}
}

func TestCreateCommandRejectsSimulationResetForPhysicalAdapter(t *testing.T) {
	service := NewService(NewMemoryStore(), intent.NewDeterministicParser())
	_, err := service.CreateCommand(context.Background(), CreateCommand{
		Request: "让1号机器人把红色方块放到交接区",
		Adapter: "xlerobot_direct",
		ExecutionContext: ExecutionContext{Mode: "simulation_demo", NewEpisode: true},
	})
	if !errors.Is(err, ErrExecutionContextUnsupported) { t.Fatalf("err=%v", err) }
}
```

Also prove an old two-field create body creates only the requested manipulation intents. Add a SQLite reopen test that creates a simulation task, closes/reopens the store, and reads the same execution context.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
go test ./tasks ./fleet ./console -run 'ExecutionContext|SimulationEpisodePreparation' -count=1
```

Expected: compilation fails because the new context, command, and action do not exist.

- [ ] **Step 3: Implement create context and deterministic prelude**

Add:

```go
var ErrExecutionContextUnsupported = errors.New("execution context is not supported by adapter")

type ExecutionContext struct {
	Mode       string `json:"mode,omitempty"`
	NewEpisode bool   `json:"newEpisode,omitempty"`
}

type CreateCommand struct {
	Request string
	Adapter string
	ExecutionContext ExecutionContext
}
```

Add `ExecutionContext ExecutionContext \`json:"executionContext,omitempty"\`` to `Task`, define `ActionPrepareSimulation = "prepare_simulation"`, and prepend without changing the top-level natural-language intent:

```go
func withSimulationPrelude(parsed manipulation.Intent, context ExecutionContext) manipulation.Intent {
	if !context.NewEpisode { return parsed }
	parsed.Sequence = append([]manipulation.Intent{{Action: manipulation.ActionPrepareSimulation, RobotID: "robot-1"}}, parsed.Tasks()...)
	return parsed
}
```

Allow `newEpisode` only for `mujoco` and `robocasa`. Decode the optional context in both servers. Keep `Create` as a wrapper calling `CreateCommand` with an empty context. Add SQLite column `execution_context_json BLOB NOT NULL DEFAULT '{}'`, include it in insert/update/select/scan and the additive migration list. MySQL already persists the complete task JSON and needs no schema change.

- [ ] **Step 4: Verify GREEN and commit**

Run:

```bash
go test ./tasks ./middleware/sqlite ./fleet ./console -count=1
git add tasks middleware/sqlite fleet/server.go fleet/server_test.go console/server.go console/server_test.go skills/manipulation/intent.go
git commit -m "feat: add repeatable simulation task context"
```

Expected: all package and compatibility tests pass.

---

### Task 2: Execute the reset prelude through registered Runtime tools

**Files:**
- Modify: `skills/manipulation/plugin.go`
- Modify: `skills/manipulation/plugin_test.go`
- Modify: `edge/agent/runner.go`
- Modify: `edge/agent/runner_test.go`
- Modify: `edge/worker/worker.go`
- Modify: `edge/worker/worker_activity_test.go`
- Modify: `fleet/coordinator/coordinator.go`
- Modify: `fleet/coordinator/coordinator_test.go`
- Modify: `sim/mujoco/tangying_sim/tools.py`
- Modify: `sim/mujoco/tangying_sim/server.py`
- Modify: `sim/mujoco/tangying_sim/world.py`
- Modify: `sim/mujoco/tests/test_tools.py`
- Modify: `sim/mujoco/tests/test_world.py`
- Modify: `sim/robocasa/tangying_robocasa/world.py`
- Modify: `sim/robocasa/tests/test_world.py`
- Modify: `sim/robocasa/tests/test_runtime.py`

**Interfaces:**
- Consumes: `prepare_simulation` from Task 1.
- Produces: `manipulation.PrepareSimulationPlan`, `simulation.reset_episode`, `verify_episode_ready`, `reset_episode()`, and `episode_ready()`.
- Guarantees: reset is exactly-once by command identity and preserves monotonic fencing.

- [ ] **Step 1: Write failing planner, worker, and Runtime tests**

```go
func TestPrepareSimulationPlanUsesRegisteredTools(t *testing.T) {
	plan := PrepareSimulationPlan("task-1", "robot-1", time.Now().Add(time.Minute))
	got := make([]string, 0, len(plan.Steps))
	for _, step := range plan.Steps { got = append(got, step.Skill) }
	want := []string{"simulation.reset_episode", "observe_scene", "verify_episode_ready"}
	if !slices.Equal(got, want) { t.Fatalf("skills=%v", got) }
}
```

Add a Worker test whose Runtime fails if `Ground` is called for the prelude and records the exact three invoked skills. Add a Coordinator test proving preparation claims no block lease and makes robot-1's handoff intent ready only after the prelude succeeds. Add Python exact-once coverage:

```python
def test_reset_episode_is_idempotent_per_runtime_command(runtime_pair):
    world, services = runtime_pair
    world.adopt_fencing_token(19)
    command = _command(services["robot-1"], "simulation.reset_episode", "", "reset-episode-1")
    command.resource_id = ""
    command.fencing_token = 0
    before = world.episode
    assert list(services["robot-1"].execute_for_test(command))[-1].code == "OK"
    assert list(services["robot-1"].execute_for_test(command))[-1].code == "OK"
    assert world.episode == before + 1
    assert world.fencing_token >= 19
    assert world.placement == "left-start-zone"
```

- [ ] **Step 2: Run tests and verify RED**

```bash
go test ./skills/manipulation ./edge/agent ./edge/worker -run 'PrepareSimulation|ResetPrelude' -count=1
PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q sim/robocasa/tests/test_world.py sim/robocasa/tests/test_runtime.py -k 'reset_episode or episode_ready'
```

Expected: the plan, tools, and readiness methods are missing.

- [ ] **Step 3: Implement preparation planning and grounding bypass**

Register `simulation.reset_episode` as a physical simulation side effect and `verify_episode_ready` as read-only. Add this exact plan shape:

```go
func PrepareSimulationPlan(taskID, robotID string, deadline time.Time) taskgraph.TaskPlan {
	approval := "approval:" + taskID + ":physical"
	reset := physicalStep(taskID, approval, deadline, robotID, "task00-", "reset_episode", "simulation.reset_episode")
	observe := taskgraph.SkillStep{ID: "task00-observe", Skill: "observe_scene", RobotID: robotID, DependsOn: []string{reset.ID}}
	verify := taskgraph.SkillStep{ID: "task00-verify_episode", Skill: "verify_episode_ready", RobotID: robotID, DependsOn: []string{observe.ID}}
	return taskgraph.TaskPlan{ID: taskID, Goal: "prepare a fresh simulation episode", Domain: "simulation", Revision: 1,
		Steps: []taskgraph.SkillStep{reset, observe, verify}, Budget: taskgraph.Budget{MaxSteps: 3, MaxRetries: 1},
		StopPolicy: taskgraph.StopPolicy{StopOnSafety: true, StopWhenEnough: true}}
}
```

In local Runner and Fleet Worker, select this plan before manipulation grounding when `intent.Action == ActionPrepareSimulation`; all preflight, activity, approval, and command identity checks remain active.

- [ ] **Step 4: Implement simulator reset and readiness tools**

```python
class ResetEpisodeTool:
    def execute(self, context, *, target_ref="", parameters=None):
        del target_ref, parameters
        context.world.reset_episode()
        return ToolResult(True, payload=context.world.episode_status())

class VerifyEpisodeReadyTool:
    def execute(self, context, *, target_ref="", parameters=None):
        del target_ref, parameters
        ready, status = context.world.episode_ready()
        return ToolResult(ready, "OK" if ready else "EPISODE_NOT_READY", payload=status)
```

In RoboCasa reset `MjData` in place, increment episode, restore both robots and block, clear held/placement state, preserve the last adopted fencing token, refresh the cache, and notify listeners outside the lock. Readiness requires idle robots, empty grippers, the block in `left-start-zone`, and the new episode observed. Implement the same method contract on `TabletopWorld` so the explicitly supported `mujoco` demo context is real rather than accepted and then failing at Runtime.

- [ ] **Step 5: Verify GREEN and commit**

```bash
go test ./skills/manipulation ./edge/agent ./edge/worker -count=1
PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q sim/mujoco/tests/test_tools.py sim/robocasa/tests/test_world.py sim/robocasa/tests/test_runtime.py
git add skills/manipulation edge/agent edge/worker sim/mujoco sim/robocasa
git commit -m "feat: reset RoboCasa episodes through registered tools"
```

Expected: the prelude is visible and exact-once, and manipulation starts from a fresh world.

---

### Task 3: Bind each RGB-D camera to the imported XLeRobot head

**Files:**
- Modify: `sim/robocasa/tangying_robocasa/composer.py`
- Modify: `sim/robocasa/tangying_robocasa/fleet_server.py`
- Modify: `sim/robocasa/tests/test_composer.py`
- Modify: `sim/robocasa/tests/test_runtime.py`
- Modify: `sim/robocasa/tangying_robocasa/visual_assets.py`
- Modify: `scripts/export_robocasa_web_assets.py`

**Interfaces:**
- Consumes: upstream `head_depth` under `head_tilt_link`.
- Produces: `robot-1__rgbd_head`, `robot-2__rgbd_head`, and operator camera `overview`.
- Guarantees: fixed world evidence cameras are removed and head pan/tilt changes the correct optical transform.

- [ ] **Step 1: Write failing camera ownership tests**

```python
@pytest.mark.robocasa
def test_rgbd_cameras_belong_to_the_correct_xlerobot_head():
    pytest.importorskip("robocasa")
    model = mujoco.MjModel.from_xml_string(compose_handoff_scene(SceneConfig(seed=7)).xml)
    for robot_id in ("robot-1", "robot-2"):
        camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, f"{robot_id}__rgbd_head")
        tilt_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, f"{robot_id}__head_tilt_link")
        body_id = int(model.cam_bodyid[camera_id])
        ancestors = set()
        while body_id > 0:
            ancestors.add(body_id)
            body_id = int(model.body_parentid[body_id])
        assert tilt_id in ancestors
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "robot-1-evidence") == -1
```

Add a test that changes only `robot-1__head_pan_joint`, calls `mj_forward`, and proves robot 1 camera matrix changes while robot 2 does not.

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q sim/robocasa/tests/test_composer.py -k 'rgbd_cameras'
```

Expected: aliases are absent and fixed evidence cameras still exist.

- [ ] **Step 3: Implement head camera composition**

Before prefixing, locate `.//body[@name='head_tilt_link']/camera[@name='head_depth']`, rename it to `rgbd_head`, preserve upstream mount position/FOV, and make the optical pose head-fixed. Remove `robot-1-evidence` and `robot-2-evidence` from `_add_handoff_semantics`; retain `overview`. Runtime cameras become `("overview", f"{robot_id}__rgbd_head")`.

Validate after compilation:

```python
def validate_head_camera(model: mujoco.MjModel, robot_id: str) -> None:
    camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, f"{robot_id}__rgbd_head")
    tilt_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, f"{robot_id}__head_tilt_link")
    if camera_id < 0 or tilt_id < 0:
        raise ValueError(f"{robot_id} RGB-D head camera is missing")
```

- [ ] **Step 4: Verify GREEN, regenerate assets, and commit**

```bash
PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q sim/robocasa/tests/test_composer.py sim/robocasa/tests/test_runtime.py -k 'camera or runtime_info'
make robocasa-web-assets
git diff --check
git add sim/robocasa scripts/export_robocasa_web_assets.py web/assets/robocasa
git commit -m "feat: mount RGB-D evidence cameras on XLeRobot heads"
```

Expected: both cameras are correct head descendants and WebGL still contains complete XLeRobot head geometry.

---

### Task 4: Add the transport-neutral sensor capture contract

**Files:**
- Create: `core/sensors/types.go`
- Create: `core/sensors/types_test.go`
- Modify: `core/telemetry/telemetry.go`
- Modify: `proto/robot/v1/robot.proto`
- Modify: `proto/fleet/v1/fleet.proto`
- Modify: `edge/robotclient/client.go`
- Modify: `edge/robotclient/telemetry_test.go`
- Modify: `edge/worker/telemetry.go`
- Modify: `edge/worker/telemetry_test.go`
- Modify: `fleet/gateway/gateway.go`
- Modify: `fleet/gateway/gateway_test.go`
- Regenerate: `gen/go/robot/v1/robot.pb.go`
- Regenerate: `gen/go/fleet/v1/fleet.pb.go`
- Regenerate: `python/tangying_robot_proto/robot/v1/robot_pb2.py`
- Regenerate: `python/tangying_robot_proto/fleet/v1/fleet_pb2.py`

**Interfaces:**
- Produces: `sensors.Capture`, `sensors.Frame`, protobuf `SensorCapture`/`SensorFrame`, and `telemetry.Snapshot.Capture`.
- Later tasks consume: immutable capture identity and RGB-D metadata/bytes.

- [ ] **Step 1: Write failing validation and mapping tests**

```go
func TestCaptureValidateRequiresCoherentRGBDIdentity(t *testing.T) {
	capture := Capture{SchemaVersion: "sensor.capture.v1", CaptureID: "capture-4-36-81",
		RobotID: "robot-1", EpisodeID: "robocasa-handoff-v1:4", SimulationStep: 36,
		SourceSequence: 81, CapturedAt: time.Now(), FrameID: "robot-1/rgbd_head",
		TransformRevision: "robocasa-world-v1", Frames: []Frame{
			{SensorID: "robot-1/rgbd_head", Modality: "rgb", MediaType: "image/png", Width: 320, Height: 240, SHA256: strings.Repeat("a", 64), Data: []byte("rgb")},
			{SensorID: "robot-1/rgbd_head", Modality: "depth", MediaType: "image/png;depth=uint16-mm", Width: 320, Height: 240, SHA256: strings.Repeat("b", 64), DepthScaleM: 0.001, Data: []byte("depth")},
		}}
	if err := capture.Validate(); err != nil { t.Fatal(err) }
}
```

Extend robotclient/worker/gateway tests so all fields and bytes survive Robot proto -> core snapshot -> Fleet proto -> Fleet sample without slice aliasing.

- [ ] **Step 2: Run tests and verify RED**

```bash
go test ./core/sensors ./edge/robotclient ./edge/worker ./fleet/gateway -run 'Capture|RGBD' -count=1
```

Expected: the package, types, and protobuf accessors do not exist.

- [ ] **Step 3: Implement shared types and validation**

```go
type Frame struct {
	SensorID string `json:"sensorId"`
	Modality string `json:"modality"`
	MediaType string `json:"mediaType"`
	Width int `json:"width"`
	Height int `json:"height"`
	SHA256 string `json:"sha256"`
	URI string `json:"uri,omitempty"`
	DepthScaleM float64 `json:"depthScaleM,omitempty"`
	MinRangeM float64 `json:"minRangeM,omitempty"`
	MaxRangeM float64 `json:"maxRangeM,omitempty"`
	Intrinsics []float64 `json:"intrinsics,omitempty"`
	CameraToWorld []float64 `json:"cameraToWorld,omitempty"`
	Data []byte `json:"-"`
}

type Capture struct {
	SchemaVersion string `json:"schemaVersion"`
	CaptureID string `json:"captureId"`
	RobotID string `json:"robotId"`
	EpisodeID string `json:"episodeId"`
	SimulationStep uint64 `json:"simulationStep"`
	SourceSequence uint64 `json:"sourceSequence"`
	CapturedAt time.Time `json:"capturedAt"`
	FrameID string `json:"frameId"`
	TransformRevision string `json:"transformRevision"`
	WorldRevision uint64 `json:"worldRevision,omitempty"`
	Frames []Frame `json:"frames"`
}
```

`Validate` checks schema, non-empty identity, positive sequence, finite calibration, unique modalities, lowercase 64-character hashes, non-empty transported bytes, and equal RGB/depth dimensions.

- [ ] **Step 4: Extend protobufs compatibly and regenerate**

Add `SensorFrame` and `SensorCapture` after existing messages. Add `SensorCapture capture = 9` to Robot `Observation` and `SensorCapture capture = 14` to Fleet `TelemetrySample`; do not renumber existing fields.

```bash
make generate
```

Expected: Go/Python sources regenerate with only the additive fields.

- [ ] **Step 5: Implement mappings and legacy fallback**

Deep-map new captures with helpers `captureToProto` and `captureFromProto`. An older unversioned `compressed_image` remains legacy-only. Invalid capture metadata is dropped with a telemetry anomaly while emergency and semantic state still flow.

Add `Capture *sensors.Capture \`json:"capture,omitempty"\`` to both `core/telemetry.Snapshot` and `fleet/telemetry.Sample`. Keep legacy `Frame` as the separately rendered operator overview; only fall back to head RGB when a Runtime omitted the legacy overview.

- [ ] **Step 6: Verify GREEN and commit**

```bash
make generate-check
go test ./core/sensors ./core/telemetry ./edge/robotclient ./edge/worker ./fleet/gateway -count=1
git add core/sensors core/telemetry proto gen/go python/tangying_robot_proto edge/robotclient edge/worker fleet/gateway
git commit -m "feat: add synchronized RGB-D capture contract"
```

Expected: generation is clean and all mappings pass.

---

### Task 5: Render one atomic MuJoCo RGB-D capture bundle per robot

**Files:**
- Modify: `sim/mujoco/tangying_sim/rendering.py`
- Modify: `sim/mujoco/tangying_sim/server.py`
- Modify: `sim/mujoco/tests/test_rendering.py`
- Modify: `sim/mujoco/tests/test_server.py`
- Modify: `sim/robocasa/tangying_robocasa/world.py`
- Modify: `sim/robocasa/tangying_robocasa/fleet_server.py`
- Modify: `sim/robocasa/tests/test_runtime.py`

**Interfaces:**
- Consumes: head cameras and protobuf capture fields.
- Produces: `RenderedCapture` and coherent `Observation.capture`.
- Guarantees: render backlog is bounded and cached pixels are never attached to newer state.

- [ ] **Step 1: Write failing RGB/depth and atomicity tests**

```python
def test_renderer_returns_rgb_and_metric_depth_from_one_camera():
    world = TabletopWorld.seeded(7)
    renderer = SceneRenderer(width=96, height=72)
    capture = renderer.render_capture(world.model, world.data, camera="overview")
    renderer.close()
    assert capture.rgb.media_type == "image/png"
    assert capture.depth.media_type == "image/png;depth=uint16-mm"
    assert _ihdr(capture.rgb.data)[:4] == (96, 72, 8, 2)
    assert _ihdr(capture.depth.data)[:4] == (96, 72, 16, 0)
    assert len(capture.intrinsics) == 9
    assert len(capture.camera_to_world) == 16
```

Add a slowed RoboCasa pick test asserting `capture.simulation_step == robot_state.step_count`, capture episode equals robot-state episode, RGB/depth share identity, and RGB hashes change during arm motion.

- [ ] **Step 2: Run tests and verify RED**

```bash
.venv/bin/pytest -q sim/mujoco/tests/test_rendering.py sim/mujoco/tests/test_server.py -k 'depth or atomic'
PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q sim/robocasa/tests/test_runtime.py -k 'capture or rgbd'
```

Expected: `render_capture` and coherent captures are absent.

- [ ] **Step 3: Implement RGB and metric-depth rendering**

```python
@dataclass(frozen=True)
class RenderedFrame:
    data: bytes
    media_type: str
    sha256: str

@dataclass(frozen=True)
class RenderedCapture:
    rgb: RenderedFrame
    depth: RenderedFrame
    width: int
    height: int
    intrinsics: tuple[float, ...]
    camera_to_world: tuple[float, ...]
```

On the renderer-owner thread, update the chosen camera once, render RGB, enable depth rendering, render metric depth, disable depth mode, encode finite in-range metres as big-endian uint16 millimetres, and encode depth PNG color type 0/bit depth 16. Derive pinhole intrinsics from FOV/dimensions and extrinsics from `data.cam_xpos`/`data.cam_xmat`.

- [ ] **Step 4: Replace the frame cache with an atomic bundle cache**

Under the world lock, copy `MjData`, world state, and entities into one request. Render outside the lock, then publish one completed bundle. Use a one-slot latest-wins pending request. Build the entire `Observation` from the completed bundle and set legacy RGB fields from its RGB member.

Use:

```python
capture_id = f"{scene_id}:{episode}:{robot_id}:{source_sequence}"
episode_id = f"{scene_id}:{episode}"
```

Robot 1 renders `robot-1__rgbd_head`; Robot 2 renders `robot-2__rgbd_head`. Render `overview` separately into the legacy compressed-image fields so `/v1/scene/frames/robot-1` remains a real MuJoCo operator overview and is never confused with head evidence.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/pytest -q sim/mujoco/tests/test_rendering.py sim/mujoco/tests/test_server.py
PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q sim/robocasa/tests/test_runtime.py
git add sim/mujoco sim/robocasa
git commit -m "feat: publish atomic MuJoCo RGB-D captures"
```

Expected: captures stay coherent and hashes change while the robot visibly articulates.

---

### Task 6: Correlate captures with Fleet world revisions and expose sensor APIs

**Files:**
- Modify: `fleet/telemetry/telemetry.go`
- Create: `fleet/telemetry/telemetry_test.go`
- Modify: `fleet/redis/telemetry.go`
- Create: `fleet/redis/telemetry_test.go`
- Modify: `fleet/gateway/gateway.go`
- Modify: `fleet/gateway/gateway_test.go`
- Modify: `proto/fleet/v1/fleet.proto`
- Modify: `edge/worker/telemetry.go`
- Modify: `fleet/server.go`
- Modify: `fleet/server_test.go`
- Modify: `fleet/auth/auth.go`
- Modify: `fleet/auth/auth_test.go`

**Interfaces:**
- Consumes: `sensors.Capture` and `worldmodel.Delta.Revision`.
- Produces: bounded capture storage, `CorrelateCapture`, sensor metadata/media endpoints, and compatibility scene-frame aliases.
- Guarantees: wrong robot/episode and duplicate/out-of-order captures cannot replace newer data.

- [ ] **Step 1: Write failing store and endpoint tests**

Test memory/Redis ingestion in sequence order `8, 7, 9`, correlate capture 9 to world revision 42, retrieve immutable RGB/depth, and assert capture 9 remains latest. Add an authenticated endpoint test:

```go
func TestSensorCaptureMetadataAndConditionalBytes(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	capture := sensors.Capture{SchemaVersion: "sensor.capture.v1", CaptureID: "capture-9",
		RobotID: "robot-1", EpisodeID: "scene:2", SimulationStep: 36, SourceSequence: 9,
		CapturedAt: time.Now().UTC(), FrameID: "robot-1/rgbd_head", TransformRevision: "scene-v1",
		Frames: []sensors.Frame{{SensorID: "robot-1/rgbd_head", Modality: "rgb", MediaType: "image/png",
			Width: 16, Height: 12, SHA256: strings.Repeat("a", 64), Data: []byte("rgb")},
			{SensorID: "robot-1/rgbd_head", Modality: "depth", MediaType: "image/png;depth=uint16-mm",
				Width: 16, Height: 12, SHA256: strings.Repeat("b", 64), DepthScaleM: 0.001, Data: []byte("depth")}}}
	if err := f.telemetry.Ingest(context.Background(), fleettelemetry.Sample{RobotID: "robot-1", Capture: &capture}); err != nil { t.Fatal(err) }
	if err := f.telemetry.CorrelateCapture(context.Background(), capture.CaptureID, 42); err != nil { t.Fatal(err) }
	metadata := f.do(t, http.MethodGet, "/v1/sensors/latest/robot-1", nil, true, false)
	view := decode[sensors.Capture](t, metadata)
	if view.WorldRevision != 42 || len(view.Frames) != 2 { t.Fatalf("view=%#v", view) }
	request, err := http.NewRequest(http.MethodGet, f.server.URL+view.Frames[0].URI, nil)
	if err != nil { t.Fatal(err) }
	request.Header.Set("Authorization", "Bearer "+f.operatorToken(t))
	request.Header.Set("If-None-Match", `"`+view.Frames[0].SHA256+`"`)
	rgb, err := http.DefaultClient.Do(request)
	if err != nil { t.Fatal(err) }
	defer rgb.Body.Close()
	if rgb.StatusCode != http.StatusNotModified { t.Fatalf("status=%d", rgb.StatusCode) }
}
```

- [ ] **Step 2: Run tests and verify RED**

```bash
go test ./fleet/telemetry ./fleet/redis ./fleet/gateway ./fleet -run 'SensorCapture|CaptureCorrelation|ConditionalBytes' -count=1
```

Expected: capture storage, correlation, and routes are missing.

- [ ] **Step 3: Add storage and world correlation**

Extend the Store contract:

```go
LatestCapture(context.Context, string) (sensors.Capture, bool, error)
Capture(context.Context, string) (sensors.Capture, bool, error)
CorrelateCapture(context.Context, string, uint64) error
```

Memory keeps 64 captures per robot and deep copies bytes. Redis uses robot-scoped sequence indexes, metadata hashes, per-modality byte keys, and a 24-hour demo TTL. Correlation stores the maximum revision and works before or after sample ingestion.

Add `capture_id`, `episode_id`, and `simulation_step` to Fleet `ObservationEnvelope`. `observationsFromSample` copies them to every envelope. After `World.Ingest`, Gateway passes the accepted delta revision to `CorrelateCapture`; multiple envelopes converge to the highest revision.

- [ ] **Step 4: Implement authenticated sensor routes**

```go
s.mux.HandleFunc("GET /v1/sensors/captures", s.listSensorCaptures)
s.mux.HandleFunc("GET /v1/sensors/latest/{robot}", s.latestSensorCapture)
s.mux.HandleFunc("GET /v1/sensors/media/{captureKey}/{modality}", s.getSensorMedia)
```

Metadata strips bytes and writes server-authored URIs. `captureKey` is base64url of capture ID. Media validates `rgb`/`depth`, sets type, quoted SHA-256 ETag, `Cache-Control: private, no-cache`, and returns 304 for matching `If-None-Match`. Operator auth can read; device auth cannot. Existing scene-frame routes alias latest RGB.

- [ ] **Step 5: Verify GREEN and commit**

```bash
make generate-check
go test -race ./fleet/telemetry ./fleet/redis ./fleet/gateway ./fleet -count=1
go test ./fleet/auth -count=1
git add fleet proto gen/go python/tangying_robot_proto edge/worker
git commit -m "feat: correlate RGB-D captures with Fleet world revisions"
```

Expected: all stores/routes pass under the race detector.

---

### Task 7: Project tool/capture synchronization and return lightweight task summaries

**Files:**
- Modify: `edge/worker/worker.go`
- Modify: `edge/worker/worker_activity_test.go`
- Modify: `tasks/experience.go`
- Modify: `tasks/experience_events.go`
- Modify: `tasks/experience_test.go`
- Modify: `tasks/service.go`
- Modify: `tasks/service_test.go`
- Modify: `tasks/repository.go`
- Modify: `tasks/memory_store.go`
- Modify: `middleware/sqlite/tasks.go`
- Modify: `middleware/sqlite/tasks_test.go`
- Modify: `fleet/mysql/store.go`
- Modify: `fleet/mysql/store_test.go`
- Modify: `fleet/server.go`
- Modify: `fleet/server_test.go`
- Modify: `console/server.go`
- Modify: `console/server_test.go`

**Interfaces:**
- Consumes: latest capture basis and correlated world revision.
- Produces: `tasks.ActivitySynchronization`, user/professional evidence fields, and `TaskSummary` list view.
- Guarantees: acknowledgement alone never projects a tool or subtask as confirmed.

- [ ] **Step 1: Write failing projection and response-size tests**

Use an activity payload:

```go
"synchronization": map[string]any{
	"episodeId": "robocasa-handoff-v1:4",
	"basisCaptureId": "capture-4-36-80",
	"latestCaptureId": "capture-4-40-84",
	"basisWorldRevision": uint64(100),
	"latestWorldRevision": uint64(112),
	"sensorFreshness": "FRESH",
}
```

Assert ordinary activity text says the environment/camera are advancing, professional details preserve IDs/revisions, and `CONFIRMED` without advancing Harness evidence stays awaiting evidence. Create 30 tasks with 100 events each; `/v1/tasks?view=summary&limit=20` must return 20 newest records, omit `events`/`plan`, and stay below 64 KiB.

- [ ] **Step 2: Run tests and verify RED**

```bash
go test ./tasks ./fleet ./console ./edge/worker -run 'Synchronization|TaskSummary|UnadvancedEvidence' -count=1
```

Expected: synchronization and summary contracts are absent.

- [ ] **Step 3: Attach bounded capture ranges to tool activities**

Track latest capture metadata under a Worker mutex on every successful `buildSample`. Snapshot `basis` before `Invoke`, then perform one bounded post-tool observation and snapshot `latest`. Add only this metadata to events:

```go
type ActivitySynchronization struct {
	EpisodeID string `json:"episodeId"`
	BasisCaptureID string `json:"basisCaptureId"`
	LatestCaptureID string `json:"latestCaptureId"`
	BasisWorldRevision uint64 `json:"basisWorldRevision"`
	LatestWorldRevision uint64 `json:"latestWorldRevision"`
	SensorFreshness string `json:"sensorFreshness"`
}
```

If a physical tool returns but neither relevant joints/object relations nor capture sequence advances, emit `RECOVERY_ACTIVITY` class `OBSERVATION_WAIT`, do not replay the tool, and leave it awaiting evidence.

- [ ] **Step 4: Project synchronization into human-first task experience**

Sanitize synchronization independently from arguments. Use exact user meanings:

- running/fresh: “机器人正在执行，环境与相机正在同步更新”;
- awaiting: “动作已结束，正在确认环境和相机证据”;
- confirmed plus Harness IDs/revision advance: “环境和传感器已经确认动作结果”;
- stale/frozen: “动作状态已收到，正在重新连接环境数据”.

Only `CONFIRMED` with Harness evidence IDs can set a revision step `SATISFIED`.

- [ ] **Step 5: Implement bounded summaries**

```go
type TaskSummary struct {
	ID string `json:"id"`
	Request string `json:"request"`
	Adapter string `json:"adapter"`
	Intent manipulation.Intent `json:"intent"`
	State taskgraph.TaskState `json:"state"`
	Approved bool `json:"approved"`
	CurrentRevision uint64 `json:"currentRevision"`
	AggregateVersion uint64 `json:"aggregateVersion"`
	UpdatedAt time.Time `json:"updatedAt"`
}
```

Add an optional fast repository boundary:

```go
type SummaryRepository interface {
	ListSummaries(context.Context, int) ([]TaskSummary, error)
}
```

`Service.ListSummaries(ctx, limit)` clamps `1..100`, uses that interface, and has a compatibility fallback for external repositories. Memory copies only summary fields. SQLite selects task columns without loading `task_events`; MySQL selects task JSON ordered `updated_at DESC LIMIT ?` and projects summaries without a second event query. Both servers return summaries only for `view=summary`; default listing remains compatible.

- [ ] **Step 6: Verify GREEN and commit**

```bash
go test ./tasks ./middleware/sqlite ./fleet/mysql ./fleet ./console ./edge/worker -count=1
git add edge/worker tasks middleware/sqlite fleet/mysql fleet/server.go fleet/server_test.go console/server.go console/server_test.go
git commit -m "feat: correlate tool progress with environment evidence"
```

Expected: synchronization projections pass and summary payload remains below the gate.

---

### Task 8: Render the human-first mission/tool timeline and synchronized RGB-D cards

**Files:**
- Modify: `web/index.html`
- Modify: `web/styles.css`
- Modify: `web/app.js`
- Modify: `web/app_test.mjs`
- Modify: `web/embed_test.go`

**Interfaces:**
- Consumes: task experience, bounded summaries, sensor latest/media APIs, and world WebSocket snapshots.
- Produces: nested tool timeline, two head RGB-D cards, explicit overview label, sync/stale states, and one Fleet session lifecycle.
- Guarantees: wrong-episode, cross-robot, out-of-order, or uncorrelated media is never labelled live.

- [ ] **Step 1: Write failing browser tests**

```js
test("RoboCasa create explicitly requests a new simulation episode", async () => {
  const harness = createHarness();
  harness.hooks.setFleetTokenForTest("operator-token");
  harness.hooks.setFleetExecutionAdapterForTest("robocasa");
  harness.element("fleet-request").value = "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块放到右侧目标区";
  let createBody;
  const jsonResponse = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
  harness.setFetch(async (url, options = {}) => {
    if (url === "/v1/tasks" && options.method === "POST") {
      createBody = JSON.parse(options.body);
      return jsonResponse(201, { id: "task-created", state: "READY", approved: false, currentRevision: 1 });
    }
    if (url === "/v1/tasks/task-created/intents") return jsonResponse(200, { intents: [], robots: [] });
    if (url === "/v1/tasks/task-created/experience") return jsonResponse(200, taskExperience({ taskId: "task-created" }));
    if (url === "/v1/tasks/task-created/approve") return jsonResponse(200, { id: "task-created", state: "EXECUTING", approved: true });
    if (url === "/v1/tasks?view=summary&limit=20") return jsonResponse(200, []);
    return jsonResponse(404, {});
  });
  await harness.hooks.createFleetTask();
  assert.deepEqual(createBody.executionContext, { mode: "simulation_demo", newEpisode: true });
});
```

Also prove:

- calling `showFleetDashboard()` twice leaves one devices/map/sensors/tasks timer each;
- logout clears timers, aborts sensor fetches, closes sockets, and revokes URLs;
- task polling uses `/v1/tasks?view=summary&limit=20`;
- lower-sequence, wrong-robot, wrong-episode, or future-world captures cannot replace a card;
- unchanged ETag creates no object URL;
- current metadata renders RGB, real depth media, freshness, capture suffix, and revision;
- the overview image still comes from `/v1/scene/frames/robot-1` and is labelled non-evidence;
- activities are grouped under matching `stepId` in stable tool order.

- [ ] **Step 2: Run tests and verify RED**

```bash
node --test web/app_test.mjs
```

Expected: context, lifecycle, sensor, and nested activity assertions fail.

- [ ] **Step 3: Add explicit RGB-D evidence markup**

Keep the world canvas largest. Add a separate `<img id="fleet-overview-rgb" alt="MuJoCo 仿真器实时总览">`, then add this structure for both robots:

```html
<figure id="fleet-sensor-robot-1" class="sensor-evidence-card" data-state="waiting">
  <figcaption><strong>1号机器人</strong><span>XLeRobot 头部 RGB-D</span></figcaption>
  <img id="fleet-rgb-robot-1" alt="1号机器人头部 RGB 相机实时证据">
  <a id="fleet-depth-robot-1" class="depth-evidence" hidden>查看真实深度图</a>
  <dl><dt>同步</dt><dd id="fleet-sensor-sync-robot-1">等待传感器</dd></dl>
</figure>
```

Label overview “MuJoCo 总览（非机器人相机证据）”, refresh it from the compatibility overview endpoint, and add nested `<ol class="mission-tools">` to mission step cards.

- [ ] **Step 4: Implement one Fleet session lifecycle**

```js
const fleetSessionLifecycle = {
  generation: 0,
  intervals: new Map(),
  controllers: new Set(),
  startInterval(name, callback, delay) {
    if (this.intervals.has(name)) return;
    this.intervals.set(name, setInterval(callback, delay));
  },
  stop() {
    this.generation += 1;
    for (const id of this.intervals.values()) clearInterval(id);
    this.intervals.clear();
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
  },
};
```

Immediate reads happen once; named intervals install once. Logout/session replacement/pagehide stops all resources. Visibility change pauses media bytes. Fleet detection uses three 750 ms attempts with 250/500 ms backoff and can recover without reloading. Task polling uses bounded summaries.

- [ ] **Step 5: Implement correlated sensor updates and nested tools**

```js
const fleetSensorState = new Map();
```

Accept metadata only when robot matches, sequence increases, episode matches active task, world revision does not exceed received world state, and capture age is fresh. Fetch changed URIs with `If-None-Match`; retain on 304 and revoke replaced URLs. Use live/stale/waiting/unavailable labels. Depth comes from the real depth PNG, never RGB colorization.

Group activities by `stepId`; render display name, purpose, robot, status, sync text, capture suffix, and world revision below the plain-language subtask. Preparation reads “准备新的仿真环境”; ordinary manipulation stays numbered 1 and 2. Mission pulse advances only from authoritative task/Harness state.

- [ ] **Step 6: Verify GREEN and commit**

```bash
node --check web/app.js
node --test web/app_test.mjs web/world_view_test.mjs
go test ./web -count=1
git add web
git commit -m "feat: show synchronized task tools and RGB-D evidence"
```

Expected: no duplicate timer, stale-frame replacement, or object-URL leak remains.

---

### Task 9: Prove repeatability, visual motion, sensor correctness, and distributed recovery end to end

**Files:**
- Modify: `tests/e2e/robocasa_harness.py`
- Modify: `tests/e2e/test_robocasa_handoff.py`
- Modify: `tests/e2e/test_robocasa_faults.py`
- Modify: `tests/e2e/test_robocasa_visual_twin.py`
- Modify: `scripts/run_robocasa_harness.py`
- Modify: `scripts/robocasa-fleet.sh`
- Modify: `docs/robocasa-handoff.md`
- Modify: `docs/user-console.md`
- Modify: `docs/production/data-contracts.md`
- Modify: `docs/production/testing-and-acceptance.md`
- Modify: `docs/production/sim-to-real.md`

**Interfaces:**
- Consumes: Tasks 1-8.
- Produces: repeat-twice acceptance, capture/world/tool trajectory evidence, fault artifacts, screenshots, and operator/Sim2Real documentation.
- Guarantees: completion is proven by fresh environment/sensor evidence.

- [ ] **Step 1: Write the failing repeated-episode test**

```python
@pytest.mark.robocasa
def test_same_stack_completes_two_new_rgbd_episodes(robocasa_stack):
    first = robocasa_stack.run_handoff(new_episode=True)
    second = robocasa_stack.run_handoff(new_episode=True)
    assert first.task["state"] == second.task["state"] == "SUCCEEDED"
    assert second.initial_capture["episodeId"] != first.initial_capture["episodeId"]
    assert second.initial_world["entities"]["red-block"]["relations"]["inside"] == "left-start-zone"
    for run in (first, second):
        assert run.final_world["entities"]["red-block"]["relations"]["inside"] == "right-target-zone"
        assert run.tool_names[:3] == ["simulation.reset_episode", "observe_scene", "verify_episode_ready"]
        assert run.rgbd_advanced_for("robot-1")
        assert run.rgbd_advanced_for("robot-2")
        assert run.world_changed_while("manipulation.pick")
        assert run.world_changed_while("manipulation.place")
```

- [ ] **Step 2: Write failing browser and fault assertions**

Require the live DOM to contain the original request, preparation, two numbered subtasks, every tool row, both RGB-D cards, fresh sync badges, changing hashes, changing WebGL joint state while a tool is running, and final success evidence.

Inject RGB failure, depth failure, duplicate/old/cross-robot/wrong-episode captures, frozen semantics, frozen camera, world WebSocket loss, Edge disconnect after command acceptance, coordinator restart, Redis pause, and multiple dashboard clients. Every case must block false satisfaction and show its recovery state.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q tests/e2e/test_robocasa_handoff.py tests/e2e/test_robocasa_visual_twin.py tests/e2e/test_robocasa_faults.py -k 'two_new_rgbd_episodes or sensor_sync or capture_fault'
```

Expected: result helpers, trajectory artifacts, and sensor fault controls are missing.

- [ ] **Step 4: Record revision-based acceptance evidence**

For every sample record task/revision/step/tool status, world revision, episode, simulation step, source sequence, joint map, block pose/relation, capture ID, RGB/depth hashes, dimensions, freshness, and browser render time. Assertions use revision ranges, not wall-clock coincidence.

Acceptance-only render faults are guarded by `FLEET_ACCEPTANCE_NONCE`; process/Redis faults remain external. Write:

```text
tasks.json
tool-activities.json
world-trajectory.json
sensor-trajectory.json
fault-matrix.json
browser-network.json
browser-state.json
screenshots/initial.png
screenshots/robot-1-running.png
screenshots/robot-2-running.png
screenshots/completed.png
```

- [ ] **Step 5: Update production/operator documentation**

Document natural-language flow, tool meanings, sensor badges, RGB versus metric depth, reset prelude, troubleshooting, `sensor.capture.v1`, endpoints, correlation, VLA observation use, calibration gates, and `SIMULATION_GO`/`PHYSICAL_GO`. Include exact curl examples that obtain a demo token without displaying credentials.

- [ ] **Step 6: Run complete verification**

```bash
make generate-check
make test-go
make test-web
make test-python
make test-robocasa-e2e
make test-robocasa-faults
make robocasa-acceptance-candidate
git diff --check
git status --short
```

Expected: all tests pass and the acceptance pack proves two successful, visibly moving, sensor-correlated episodes.

- [ ] **Step 7: Verify the real browser demo and commit**

```bash
bash scripts/robocasa-fleet.sh stop
ROBOCASA_HUMAN_SPEED=0.30 bash scripts/robocasa-fleet.sh run
```

At `http://127.0.0.1:18080/`, submit the exact Chinese task twice. Verify both complete; both robots visibly articulate during their tools; both RGB-D cards change with motion; tool rows stay synchronized; the second run starts fresh.

```bash
git add tests/e2e scripts/run_robocasa_harness.py scripts/robocasa-fleet.sh docs
git commit -m "test: prove synchronized repeatable RoboCasa handoff"
```

---

## Final verification gate

Before claiming completion require all facts below:

- two consecutive tasks in one stack are `SUCCEEDED`;
- episode ID changes and both runs start at `left-start-zone`;
- both runs end at `right-target-zone` with neither robot holding the block;
- intent nodes reach `SATISFIED` only after Harness evidence;
- every registered prelude/manipulation tool appears in browser and domain events;
- physical tool running ranges contain joint/object revision changes;
- Robot 1 and Robot 2 RGB/depth share capture identities and advance during motion;
- no card accepts out-of-order, wrong-robot, wrong-episode, or future-world frames;
- WebGL, mission rail, cards, world endpoint, and final task state agree on episode/revision;
- health and Redis lease latency remain inside the acceptance gates under multi-tab load;
- with four visible browser clients, `/healthz` p95 stays below 1 second, task-summary p95 stays below 500 ms, task summaries stay below 64 KiB, and no coordinator leadership renewal is lost;
- only intended changes are committed and acceptance artifacts remain ignored.
