# Tangying Robot Agent OS

Distributed Agent control plane and edge runtime for embodied robots. The v0.1 release candidate turns “把红色杯子放进右侧收纳盒” into a typed, guarded tabletop manipulation graph and executes the same Robot Gateway contract against MuJoCo or an XLeRobot Raspberry Pi edge.

```text
Natural language -> Cloud Agent Core -> Mac Local Agent -> Robot Gateway -> ROS 2 -> XLeRobot
                                                  \-> MuJoCo
```

## Implemented in v0.1.0-rc.1

- Domain-independent TaskGraph, Skill Manifest, Guard, Compiler, trace model, budgets, approvals, leases, and idempotency.
- Versioned protobuf/gRPC contracts for the cloud control plane and Robot Gateway.
- Chinese and English deterministic tabletop pick-and-place intent parsing.
- PostgreSQL-backed cloud API, WebSocket event stream, approval, cancellation, claims, and operator page.
- Restart-safe macOS Local Agent with SQLite and mTLS-by-default Robot Gateway client.
- Headless MuJoCo tabletop world and Robot Gateway adapter.
- Raspberry Pi gateway, Safety Supervisor, ROS 2 Jazzy action packages, and watchdog.
- Fail-closed XLeRobot two-wheel adapter pinned to upstream commit `3d14695e40c9c68229c0aacffca6053c75cd3eb6`.

## Safety boundary

Cloud models issue high-level skills only. The Raspberry Pi owns hardware validation, command leases, range checks, cancellation, emergency-stop latching, and final execution permission. A physical emergency stop that removes actuator power is required before hardware trials.

The simulation acceptance suite is automated. Physical XLeRobot success rates and stop timing are not claimed until the hardware checklist and 30-trial test have been executed on the user's robot.

## Development

```bash
make setup
make generate
make test
.venv/bin/python scripts/run_simulation_acceptance.py --episodes 30 --seed 20260817
```

See [simulation quickstart](docs/quickstart.md), [architecture](docs/architecture.md), [protocol invariants](docs/protocols.md), [XLeRobot setup](docs/xlerobot-setup.md), and the mandatory [hardware safety checklist](docs/safety-checklist.md).
