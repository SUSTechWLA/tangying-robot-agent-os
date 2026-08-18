# Architecture

## Target layering

```text
User
  ↓
LLM / VLM Agent                  natural language, tool choice, task recovery
  ↓
Cloud Control Plane              approval, task state, leases, audit
  ↓ HTTPS / WebSocket
Laptop Local Agent               grounding, deterministic plan, resumable steps
  ↓ Robot Runtime API (Go: edge/runtime)
Robot Runtime Client             capability check, timeouts, cancel, estop
  ↓ mTLS gRPC RobotGateway
Raspberry Pi Robot Gateway       Safety Supervisor + capability descriptors
  ↓ RobotBackend
ROS 2 adapter / XLeRobot direct  internal integration and ecosystem
  ↓ ROS 2 action / USB serial
XLeRobot driver / MCU / motors
```

The Agent decides **what** should happen. The Robot Runtime decides **whether and
how it may execute right now**, and the ROS 2/driver layer executes **only
deterministic, bounded commands**.

## Boundaries

- Cloud Agent converts language into a validated `manipulation.Intent`. Compound
  one-sentence requests are split into an ordered `sequence` of intents. The
  Agent never sees robot topics, actions or raw sensor streams.
- Local Agent expands each intent into a versioned skill graph with prefixed
  step ids, approval, deadline, lease and idempotency keys. It renews the cloud
  task lease while long-running sequences execute and cancels local execution
  when renewal fails. `edge/runtime` is the only Go package that models the
  Robot Runtime contract; `edge/robotclient` is one transport implementation.
- Robot Gateway validates every command with `SafetySupervisor` before backend
  dispatch. ROS 2 is one optional backend, never the Agent API.
- `CapabilityInfo` descriptors in `RobotCapabilities` tell the Agent what is
  available, whether it is currently blocked, and its safety level. The Local
  Agent refreshes this snapshot before execution and fails closed when a
  required capability is unknown or unavailable.
- `Observation.semantic_state` carries low-rate semantic state
  (`IDLE` / `EXECUTING` / `EMERGENCY_STOPPED`). Raw camera, LiDAR, IMU, motor
  and joint data stays inside perception, ROS 2 and driver processes.

## Safety path

The deterministic safety path is independent of the LLM and cannot be bypassed
by tool choice:

1. Cloud approval for physical tasks.
2. Local `guard` validation: known skill, lease, deadline, approval and
   grounding confidence.
3. Runtime capability snapshot: capability exists and is currently available.
4. `SafetySupervisor`: schema, task/command id, skill allowlist, deadline,
   lease bound, idempotency key, safety profile, approval, bounded action
   chunks (no mobile-base keys, allowed joint keys and value ranges only).
5. Backend/driver checks: calibration, ports, action key/value bounds.
6. Local operator is the only path to clear an E-stop latch.

## Protocols

- Cloud ↔ Local: HTTPS/WebSocket.
- Local ↔ Robot Runtime: mTLS gRPC.
- Robot Runtime ↔ ROS 2: ROS action/topic inside the robot.
- Robot Runtime/driver ↔ hardware: USB serial (XLeRobot control boards).

MuJoCo and XLeRobot expose the same `RobotGateway` protobuf service. The first
physical release can use `XLeRobotDirectBackend` without ROS2; the existing
ROS2 packages remain optional for future ROS-native perception and navigation.

See `proto/robot/v1/robot.proto` for the device contract,
`proto/controlplane/v1/controlplane.proto` for the distributed task contract,
and [Agent and Sim2Real](agent-v1.md) for the v1 upgrade contract.
