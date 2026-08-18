# Changelog

## v1-dev (current upgrade)

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
