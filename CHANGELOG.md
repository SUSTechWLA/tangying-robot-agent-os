# Changelog

## v1-dev (current upgrade)

- 云端成为联网机器人主要产品形态；Local Brain 保留为无网络部署，两者共用 `world.snapshot.v1`、工具目录、Observation Registry 与 Robot Runtime 契约。
- 新增单逻辑红色方块的双 MuJoCo 机器人交接：交接区世界证据、单调资源 fencing、`BLOCK_AVAILABLE/BLOCK_DELIVERED` 领域事件、1 个真实进程断连恢复与 8 个确定性故障边界。
- 新增权威实时 WorldHub 与交互式 3D Console：一次性 WebSocket 票据、游标重放/缺口重同步、左键平移、右键旋转、指针锚定缩放和来源新鲜度/资源归属展示。
- 实机 XLeRobot Runtime 现在在运动前验证 robot/catalog/world/resource/fencing 身份；缺少环境观测或放置验证器时 manipulation fail-closed。

- 新增 Fleet 云端一键 Docker 部署：mysql + redis + fleet-control-plane + nginx（HTTPS 控制台 + 8444 mTLS gRPC 透传），8080 仅 expose 于 Docker 内网、不发布到宿主机。
- 新增 edge-worker：从 Redis Stream 直连或 HTTP 长轮询拉取任务，连接 Robot Runtime，上报状态/事件/遥测。
- 新增机器人公网接入 mTLS gRPC 网关（FleetGateway）：Register/Link 双向流、断线指数退避重连、心跳与设备租约（过期自动离线）、服务器命令下行（急停/取消）。
- 新增云端协调器：意图级多机器人任务图、事件驱动跨机器人刷新与重新投递、意图声明租约超时自动回收。
- 新增多机器人全局地图融合（占用栅格 + 轨迹 + 实体）与云端 Console（登录/设备/任务/遥测/地图）。
- 新增双 MuJoCo 仿真场景：`--robot-id`/`--xml` 参数与世界偏移 r2 场景生成脚本（`scripts/gen_scene_variant.py`）。
- 新增认证边界：操作员 Bearer token（HMAC，24h）、每机器人独立且绑定 `X-Robot-ID` 的设备凭据（`fleet/auth`）、nginx 客户端 IP 白名单（`FLEET_ALLOWED_CIDRS`）。
- 新增实时上帝视角（God View）：Robot Runtime 场景帧经 edge-worker 500ms 周期上行（HTTP base64 / mTLS gRPC 原始字节），`/v1/scene/frames` 提供双机实时画面；`/v1/maps/global` 扩展 held/placements 语义；`/v1/world` 输出机器可读世界状态供 harness agent 编排。
- 仿真可观测性：`--human-speed` 墙钟节流让技能动画以可观看速度执行（默认 0 不影响验收）；世界锁内滚动快照（~25Hz）使观察无阻塞，执行期间画面/物体/held 持续可见。
- Added pure distributed AgentOS brain/isolation design: controlplane.Brain, edge/runtime.Router, per-step RobotID, and event-driven GraphRuntime node refresh while keeping robot runtime unaware of command origin.
- Added Alibaba Cloud Fleet control plane: Go HTTP API, MySQL task repository, Redis cache/stream queue, Docker Compose one-click deployment and deploy-alicloud.sh.

- Added pure distributed AgentOS brain/isolation design: , , per-step , and event-driven  node refresh while keeping robot runtime unaware of command origin.

- Added explicit Agent / Robot Runtime / Middleware / ROS 2 / real-time / hardware boundaries, with executable dependency tests that prevent concrete infrastructure and transport SDKs from entering Agent core packages.
- Added vendor-neutral Middleware ports for task/execution state, bounded queues, events, cache, locks and traces; moved the default WAL SQLite store under `middleware/sqlite` and injected the in-memory queue at the composition root.
- Replaced task-graph-aware robot execution with semantic Runtime commands and capability names; protobuf/gRPC mapping now stays in `edge/robotclient` and the Python Runtime service boundary.
- Decoupled Safety, XLeRobot direct, and ROS 2 backends from generated protobuf types using transport-neutral Runtime models, while keeping high-rate camera/LiDAR/IMU/joint data robot-local.
- Preserved the layered architecture specification, implementation plan, and middleware adapter guide as versioned development design assets; PostgreSQL, Redis, and Kafka remain optional future adapters rather than default dependencies.
- Replaced the hosted control plane with one laptop Local Agent process serving Console/API, LLM orchestration, task execution and SQLite persistence; removed the cloud binary, PostgreSQL store, Compose stack and cloud installer role.
- Simplified laptop-to-Raspberry-Pi operation to direct mTLS gRPC initiated by the laptop, while the Pi keeps only the bounded command/E-stop safety journal.
- Preserved the approved local-first architecture specification and delivery plan as durable design assets linked from the current architecture documentation.
- Added LLM self-orchestration: the planner chooses and orders skills from the registered catalog, with deterministic fallback, self-consistency voting and `/v1/orchestration/metrics` quality scoring.
- Added a user-facing Robot Agent Console: natural-language task creation, live task/audit views, Robot Runtime and sensor/semantic telemetry, a MuJoCo top-down scene renderer and orchestration metrics.
- Added a Local Agent telemetry bridge (`POST /v1/telemetry` / `GET /v1/telemetry`) so simulation and future real XLeRobot sensor state are observable in the same console.
- Added an explicit XLeRobot production go/no-go gate (`robot-agent production-check robot-pi`) requiring providers and recorded 30-trial safety evidence.
- Added a pluggable task Agent: deterministic parser plus optional OpenAI-compatible function calling with deterministic fallback.
- Added the `fetch` tool ("把红色杯子拿过来") and a front `delivery_tray` in MuJoCo for closed-loop fetch simulation.
- Added compound one-sentence task sequences ("先放 A，再把 B 拿过来") with deterministic parsing, multi-tool OpenAI planning, per-subtask skill graphs and resumable execution.
- Expanded MuJoCo to all advertised objects (red/blue/green cups, bottles and blocks), both storage bins and the delivery tray, plus an 18-goal object/destination acceptance matrix.
- Added `make sim2real-check`, `make deploy-robot-pi` and `scripts/robot-pi-quick-deploy.sh` for repeatable simulation acceptance and fast Raspberry Pi direct-edge installation.
- Added a ROS2-free `XLeRobotDirectBackend` and `tangying_robot_gateway.run_direct_edge` so the first real XLeRobot release no longer requires ROS2.
- Added explicit entity, policy and verifier provider hooks that fail closed until real perception and policy are installed.
- Added a ROS2-free Raspberry Pi systemd template and the V1 Agent / Sim2Real contract document.
- Added the `edge/runtime` Robot Runtime boundary: structured `CapabilityInfo`, runtime availability checks before every task, per-command deadline enforcement, cancel and emergency-stop client methods.
- Added low-rate `SemanticState` to observations so Agent code sees activity and safety status instead of raw sensor streams.
- Hardened `SafetySupervisor` with command identity checks, lease bounds, bounded action-chunk key/value validation and controlled cancellation that remains distinct from the E-stop latch.
- Added structured capability descriptors to MuJoCo, XLeRobot direct and ROS 2 backends; ROS 2 read-only skills now stay on the gateway side of the ROS boundary.
- Hardened XLeRobot for physical experiments: configurable `max_relative_target` / action-chunk length, thread-safe fail-closed driver, local stop latch, provider exception mapping, graceful service shutdown, and a no-motion XLeRobot preflight.
- Added the XLeRobot experiment runbook for first physical motion, E-stop drills, provider contracts and post-stop service restart.

## v0.1.0-rc.2 - 2026-08-17

- Added one role-based installer for simulation, cloud, laptop Local Agent, and Raspberry Pi Robot Edge.
- Added the `robot-agent` lifecycle, configuration, diagnosis, pairing, and simulation-demo CLI.
- Added a bounded full-process MuJoCo demo and loopback-safe cloud defaults.
- Added laptop-to-Pi P-256 mTLS pairing with local-only CA custody and explicit trust-root rotation.
- Replaced the prototype Raspberry Pi units with hardened XLeRobot and Robot Edge services, localhost ROS discovery, stable dialout udev aliases, and no-motion preflight.
- Pinned XLeRobot and LeRobot integration, added an explicit interactive calibration tool, and fail closed without the exact calibration file or policy action chunks.
- Added endpoint runbooks for fresh installation, startup, upgrades, recovery, and the no-STM32 XLeRobot topology.

This release candidate has automated simulation evidence only. Stable `v0.1.0` still requires the physical emergency stop, network interruption checks, local perception/policy integration, and 30 hardware trials in `docs/safety-checklist.md`.

## v0.1.0-rc.1 - 2026-08-17

- Extracted a reusable distributed Agent Core from the Tangying video production architecture.
- Added natural-language tabletop pick-and-place intent parsing and manipulation skill graphs.
- Added cloud orchestration, Local Agent execution, Robot Gateway contracts, and operator controls.
- Added MuJoCo simulation, Raspberry Pi ROS 2 packages, Safety Supervisor, and XLeRobot adapter.
- Added contract, restart, safety, simulator, API, and full-process end-to-end tests.

This release candidate has automated simulation evidence only. Stable `v0.1.0` requires the physical emergency stop, network interruption checks, and 30 hardware trials described in `docs/safety-checklist.md`.
