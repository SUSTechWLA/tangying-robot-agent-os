# Home Mobile Manipulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在家庭 RGB-D 仿真中执行“导航到房间→视觉目标解析→抓取/放置→验证→返回”的单机器人自然语言任务。

**Architecture:** 保留 `home` 的纯导航合同，新增 `home_task` 场景和视觉可见的厨房工位。语言层生成带房间路线的 `home_manipulation` intent；Grounder 只把语义选择器映射为稳定引用和房间 waypoint，Runtime 在每个操作检查点用 RGB-D 重新确认。Planner 把每个能力编译成独立工具步骤，复用现有审批、幂等、journal、证据和恢复链路。

**Tech Stack:** Go 1.26、Python 3.11、MuJoCo RGB-D Runtime、现有 protobuf Robot Runtime、SQLite task journal、pytest/go test。

**Spec:** `docs/superpowers/specs/2026-09-10-home-mobile-manipulation.md`

## Global Constraints

- 只允许双 RGB-D 和机器人自身状态作为环境输入；房间标签只参与规划，不作为传感器事实。
- 每个物理工具要求 `simulation`/现场批准、有限租约、幂等键和安全 profile。
- 导航只在空载收拢状态执行；抓取和放置后必须有新鲜验证证据。
- 未知、过期、不可见和不确定物理结果保持停止或人工核对，不自动重放。

### Task 1: Extend the intent and plan contract

**Files:**
- Modify: `skills/manipulation/intent.go`
- Modify: `agent/intent/parser.go`
- Modify: `skills/manipulation/plugin.go`
- Modify: `edge/robotclient/client.go`
- Test: `agent/intent/home_route_test.go`
- Test: `skills/manipulation/plugin_test.go`

- [ ] Write a failing parser test for the kitchen transfer sentence and assert `home_manipulation`, object selector, destination selector, and ordered rooms.
- [ ] Run `go test ./agent/intent ./skills/manipulation` and confirm the new test fails because the action and parser branch do not exist.
- [ ] Add `ActionHomeManipulation`, `Intent.RouteRooms`, and planner branch that emits independent navigation/verification/manipulation steps.
- [ ] Add deterministic parser branch before pure home-route parsing; reject missing object, destination, or fewer than two rooms with clarification.
- [ ] Ground the home manipulation selector to stable IDs only after checking the mobile capability; keep object/destination visibility checks in Runtime tool execution.
- [ ] Run the focused Go tests and then `go test ./agent/... ./skills/... ./edge/robotclient`.

### Task 2: Add the home manipulation RGB-D scene

**Files:**
- Modify: `sim/mujoco/assets/xlerobot_home.xml` or create the focused `sim/mujoco/assets/xlerobot_home_task.xml`
- Modify: `sim/mujoco/tangying_sim/home_scene.py`
- Modify: `sim/mujoco/tangying_sim/rgbd_navigation.py`
- Modify: `sim/mujoco/tangying_sim/rgbd_runtime.py`
- Modify: `sim/mujoco/tangying_sim/rgbd_perception.py` or create `sim/mujoco/tangying_sim/home_task_perception.py`
- Test: `sim/mujoco/tests/test_home_scene.py`

- [ ] Write a failing model test requiring a kitchen task table, red cup, blue kitchen bin, and a navigable living-room start pose.
- [ ] Run the focused Python test and confirm the new scene validation fails.
- [ ] Add the scene geometry and free bodies with calibrated colors, collision surfaces, and no semantic truth emission.
- [ ] Implement visual color/geometry detection in world coordinates; emit only entities visible in the current RGB-D frame.
- [ ] Configure home task reset, object placement, destination mapping, arm reach, and post-release settling without teleporting released objects.
- [ ] Run `pytest sim/mujoco/tests/test_home_scene.py sim/mujoco/tests/test_rgbd_navigation.py -q`.

### Task 3: Wire scene selection and end-to-end acceptance

**Files:**
- Modify: `sim/mujoco/tangying_sim/server.py`
- Modify: `scripts/sim-stack.sh`
- Modify: `scripts/navigation-stack.sh`
- Modify: `Makefile`
- Create: `scripts/run_home_mobile_manipulation.py`
- Test: `tests/e2e/test_home_mobile_manipulation.py`

- [ ] Write a failing process-level acceptance test that starts `home_task`, creates and approves the natural-language task, and requires terminal success with each expected tool activity.
- [ ] Run the test and confirm scene selection or tool execution fails before implementation.
- [ ] Allow `home_task` through the simulator and stack argument validation while preserving `home` behavior.
- [ ] Add an acceptance script that polls task state, saves task/experience/observation evidence, and never repeats a physical command after timeout.
- [ ] Run the focused acceptance test and verify at least one navigation move, one pick, one place, fresh arrival checks, and final relation.
- [ ] Run the full relevant regression suites: `go test ./agent/... ./edge/... ./skills/... ./tasks/...`, `pytest sim/mujoco/tests tests/e2e -q` with optional dependencies respected.

### Task 4: Document the production boundary

**Files:**
- Modify: `README.md`
- Modify: `docs/user-console.md`
- Modify: `docs/guides/home-scene-operations.md`
- Modify: `docs/guides/gazebo-house-operations.md`
- Modify: `docs/production/v1-release-status.md`

- [ ] Add the exact startup, natural-language request, approval, task-history, pause/resume, and evidence commands.
- [ ] Document the scene boundary: MuJoCo `home_task` is the complete manipulation acceptance; Gazebo remains the ROS 2 navigation backend until a matching arm driver is attached.
- [ ] Record measured test results and explicitly separate `SIMULATION_GO` from physical `PHYSICAL_GO`.
- [ ] Run docs link/structure checks and `git diff --check`.
