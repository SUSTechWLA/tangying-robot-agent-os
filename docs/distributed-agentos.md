# Pure Distributed AgentOS Architecture

## Goal

One system can operate as:

- Local-only brain on a laptop.
- Cloud brain managing many sold robots over the public internet.
- Hybrid: cloud plans, edge executes, local console observes.

The robot does not know and must not care whether a capability command came from
a cloud service, a local brain, or an operator tool.

## Layers

```text
┌──────────────────────────────────────────────────────────┐
│ User / Developer Console                                  │
│ Web UI, Electron, mobile app                              │
│ Responsibilities: dialogue, plan review, telemetry/map    │
└───────────────────────────┬──────────────────────────────┘
                            │ HTTPS / WebSocket / MQTT
┌───────────────────────────▼──────────────────────────────┐
│ Distributed Control Plane (Brain)                         │
│ - LLM intent and orchestration                            │
│ - multi-robot TaskGraph                                   │
│ - approval, fleet, observability                          │
│ - MySQL / Redis / MQ / object storage                     │
└───────────────────────────┬──────────────────────────────┘
                            │ HTTP/gRPC/MQTT task contract
┌───────────────────────────▼──────────────────────────────┐
│ Edge Agent (optional local brain)                         │
│ - TaskSource adapter: cloud queue or local SQLite         │
│ - deterministic materialization                           │
│ - Robot Runtime Router                                    │
│ - lease, retry, recovery, telemetry uplink                │
└───────────────────────────┬──────────────────────────────┘
                            │ mTLS gRPC RobotRuntime
┌───────────────────────────▼──────────────────────────────┐
│ Robot Runtime                                             │
│ - capabilities, observations, skill execution             │
│ - Safety Supervisor                                       │
│ - direct XLeRobot / ROS 2 backend                         │
└───────────────────────────┬──────────────────────────────┘
                            │ USB / CAN / EtherCAT / ROS 2
┌───────────────────────────▼──────────────────────────────┐
│ Hardware / realtime controller                            │
└──────────────────────────────────────────────────────────┘
```

## Brain isolation invariant

`controlplane.Brain` is the only planning boundary.

`edge/runtime.Command` is the only execution boundary.

The wire protocol does not carry `brain_id` or `source`. A Robot Runtime cannot
tell and cannot depend on who created a command. It only sees:

- schema version
- task id
- command id
- capability
- parameters
- deadline
- lease
- idempotency key
- safety profile
- approval id

This is why a robot can be controlled by a local laptop today and a cloud
fleet tomorrow without changing robot firmware.

## Multi-robot node refresh

`core/taskgraph.GraphRuntime` is the event-driven runtime graph:

```text
node-a on robot-1 completed
  -> refresh dependents
  -> node-b on robot-2 becomes READY
```

Every `SkillStep` may carry:

```go
RobotID string
BrainID string
```

`edge/runtime.Router` maps `Command.RobotID` to a registered runtime client.
The default local installation registers one robot under `robot-local`.
A fleet control plane registers many robots under stable ids.

## Deployment profiles

| Profile | Brain | Edge Agent | Robot |
|---|---|---|---|
| Local developer | local brain | local worker | one MuJoCo/XLeRobot |
| Home user | cloud brain | thin edge worker | one XLeRobot |
| Fleet / paper | cloud brain + Redis/MQ | edge workers | many robots |
| Hybrid | cloud plans, local fallback brain | edge worker with failover | one or many robots |

## Current implementation

Already present:

- `controlplane.Brain` and `LocalBrain`
- `edge/runtime.Router`
- `SkillStep.RobotID`
- `runtime.Command.RobotID`
- `core/taskgraph.GraphRuntime` event-driven refresh
- `cmd/local-agent` wires the local robot through a Router

Still needed for full fleet deployment:

- cloud control-plane HTTP/gRPC server and database adapters
- edge TaskSource client for cloud queue
- MQTT/gRPC streaming telemetry
- global map fusion and multi-robot console
- distributed fencing for shared workspaces

The previous `tangying-ai-operation-system` video-agent repository has been
removed from this workspace; its cloud/local split informed this design but its
video workflows are intentionally not part of the robot AgentOS.
