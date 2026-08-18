# V1 Agent and Sim2Real Contract

## Scope

The first upgraded version keeps one hard promise: every agent feature must pass
the MuJoCo closed loop before it is allowed to talk to XLeRobot. The physical
XLeRobot path is prepared through the same `RobotGateway` protobuf contract and
a new ROS2-free direct backend, but it fails closed until perception, policy and
verification providers are installed on the robot.

## Agent

`cloud/agent` wraps two planners behind one parser interface:

1. `deterministic` (default): rule-based Chinese/English parsing for two tools
   and ordered compound requests.
   - `pick_and_place`: "把红色杯子放进右侧收纳盒"
   - `fetch`: "让xlerobot把红色杯子拿过来" / "bring me the blue cup"
   - sequence: "把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来"
2. `openai`: an OpenAI-compatible `chat/completions` endpoint with function
   calling. Multiple tool calls are preserved in execution order. If the model
   is unavailable or returns an invalid tool call, the agent falls back to the
   deterministic parser.

Cloud deployment can enable the model with:

```bash
AGENT_PROVIDER=openai
AGENT_BASE_URL=https://your-provider.example/v1
AGENT_API_KEY=...
AGENT_MODEL=your-model
```

The agent returns a validated `manipulation.Intent` whose `sequence` contains
every subtask. The Local Agent grounds, plans, executes and verifies each
subtask in order; step ids and idempotency keys are prefixed so a partial
sequence resumes without repeating completed physical actions. The existing
task state machine, approval gate and deterministic skill guard are unchanged;
the Local Agent now also refreshes a Robot Runtime capability snapshot before
each planned subtask.

## Robot Runtime boundary

`edge/runtime` is the Agent-facing Robot Runtime contract. `edge/robotclient`
is a transport implementation over the `RobotGateway` gRPC service. The Local
Agent asks for the current capability snapshot and fails closed when a planned
capability is unknown or unavailable. Each command also gets a client-side
deadline; `Cancel` and `EmergencyStop` are exposed as runtime methods, with
cancel kept separate from the latched safety stop.

`RobotCapabilities.capabilities` now carries structured `CapabilityInfo`
descriptors (availability, blockers, safety level, cancellation/recovery flags,
default timeout). `Observation.semantic_state` carries low-rate runtime state
such as `IDLE`, `EXECUTING` or `EMERGENCY_STOPPED`; raw camera, LiDAR, IMU and
joint streams remain inside the robot processes.

## Simulation first

Run the closed-loop acceptance tests before touching hardware:

```bash
GOCACHE=$(mktemp -d) go test ./...
.venv/bin/python -m pytest -q
```

The simulation now contains a `delivery_tray` at `front_side`, so the fetch
intent is verified end-to-end in MuJoCo just like pick-and-place.

## XLeRobot direct backend

`robot/gateway/tangying_robot_gateway/xlerobot_backend.py` implements
`RobotBackend` without ROS2:

```text
Local Agent --mTLS gRPC--> RobotGatewayService
                               |
                         XLeRobotDirectBackend
                               |
                         XLeRobotDriver (LeRobot)
```

Start it with:

```bash
.venv/bin/python -m tangying_robot_gateway.run_direct_edge \
  --listen 0.0.0.0:50051 \
  --entity-provider my_perception.providers:scene_entities \
  --policy-provider my_policy.providers:action_chunk \
  --verifier-provider my_perception.providers:verify
```

The three providers are optional at startup but required for a successful
physical task:

- `entity_provider` returns scene entity dicts for `Observe` grounding.
- `policy_provider` returns a bounded `action_chunk` list for physical skills.
- `verifier_provider` returns a `BackendResult` for `verify_grasp` and
  `verify_placement`.

Without them the backend reports `POLICY_ACTION_CHUNK_REQUIRED` or
`VERIFICATION_UNAVAILABLE` and never manufactures physical success.

A ROS2-free systemd template is available at
`deploy/raspberry-pi/tangying-robot-edge-direct.service`. Install that variant
with:

```bash
ROBOT_AGENT_DIRECT_EDGE=1 ./install.sh robot-pi --yes
```

## ROS2 policy

ROS2 is no longer on the critical path. The existing ROS2 packages remain for
future ROS-native perception and navigation integration, but the first
XLeRobot release should use the direct backend to reduce deployment and
debugging variables.
