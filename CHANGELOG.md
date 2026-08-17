# Changelog

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
