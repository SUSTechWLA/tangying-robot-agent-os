# Architecture

```text
Cloud Control Plane
  HTTPS / WebSocket
Laptop Local Agent
  mTLS gRPC
Raspberry Pi Robot Gateway + Safety Supervisor
  local ROS 2 graph
XLeRobot hardware
```

The cloud parses language and persists task state. The Mac grounds scene entities, runs local policy inference, and sequences versioned skills. The Raspberry Pi rejects unsafe, stale, unapproved, or unknown commands and owns the watchdog and stop latch.

MuJoCo and XLeRobot expose the same `RobotGateway` protobuf service. ROS 2 is not exposed to the cloud or required on macOS.

See `proto/robot/v1/robot.proto` for the device contract and `proto/controlplane/v1/controlplane.proto` for the distributed task contract.
