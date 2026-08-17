# Role-Based Installation and Quickstart Design

**Status:** Approved design pending written-spec review  
**Date:** 2026-08-17  
**Repository:** `SUSTechWLA/tangying-robot-agent-os`

## 1. Goal

Make Tangying Robot Agent OS installable on a fresh supported machine through one auditable entry point, then guide a new user from simulation to a cloud, laptop, and Raspberry Pi deployment without requiring knowledge of the repository layout.

The product command is `robot-agent`, derived from the repository name. The installation system must never imply that STS3215 servos form a separate programmable STM32 endpoint. XLeRobot hardware follows the official topology: the Raspberry Pi or laptop connects to two motor control boards over USB, and those boards communicate with the 12 V STS3215 serial-bus servos.

## 2. Supported Roles

One repository supports four install roles:

| Role | Intended machine | Supported platform | Installed runtime |
| --- | --- | --- | --- |
| `sim` | Developer laptop or workstation | macOS 13+, Ubuntu 22.04/24.04 x86_64 or arm64 | Cloud dev server, Local Agent, MuJoCo gateway, operator UI |
| `cloud` | Self-hosted cloud VM | Ubuntu 22.04/24.04 or Debian 12 x86_64/arm64 | Docker Engine, PostgreSQL, cloud control plane |
| `local` | User laptop | macOS 13+ or Ubuntu 22.04/24.04 x86_64/arm64 | `robot-agent`, Local Agent, SQLite state, pairing credentials |
| `robot-pi` | XLeRobot Raspberry Pi 4/5 | Ubuntu 24.04 arm64 | ROS 2 Jazzy workspace, Robot Gateway, Safety Supervisor, XLeRobot adapter, systemd units |

Unsupported operating systems fail before changing the machine and print the support matrix. Raspberry Pi OS is not supported in the first installer because ROS 2 Jazzy is not a native Tier 1 target there. Windows users run the simulation through WSL2 Ubuntu; physical USB hardware remains outside the first Windows scope.

## 3. Installation Entry Points

The root `install.sh` is the only bootstrap entry point:

```bash
./install.sh sim
./install.sh cloud
./install.sh local
./install.sh robot-pi
```

Because the GitHub repository is private, a fresh machine authenticates with GitHub CLI before cloning. The reviewed bootstrap workflow is:

```bash
gh auth login
gh repo clone SUSTechWLA/tangying-robot-agent-os -- --branch codex/v0.1
cd tangying-robot-agent-os
less install.sh
./install.sh sim
```

The bootstrap script runs from an authenticated checkout and records its exact commit. `--version` can switch to another fetched tag or branch after confirming the worktree has no changes. `--yes` enables noninteractive package installation. Without `--yes`, privileged or invasive changes require confirmation. The documentation never places a GitHub token in a command line or pipes an unauthenticated remote script into a privileged shell.

Role implementations live under `scripts/install/`:

```text
scripts/install/
  common.sh
  sim.sh
  cloud.sh
  local.sh
  robot-pi.sh
```

Every installer is idempotent, uses strict shell mode, records a machine-readable receipt, and may be rerun after partial failure. It does not delete user data or replace an existing configuration without an explicit backup.

## 4. Unified `robot-agent` Command

The Go command `cmd/robot-agent` provides a stable user interface while existing service binaries remain implementation details:

```text
robot-agent doctor [--role ROLE]
robot-agent configure [--role ROLE]
robot-agent start [ROLE]
robot-agent stop [ROLE]
robot-agent restart [ROLE]
robot-agent status [ROLE]
robot-agent logs [ROLE] [--follow]
robot-agent demo
robot-agent pair ROBOT_HOST [--ssh-user USER]
robot-agent version
```

The command reads an installation receipt to determine the local role and dispatches only an allowlisted set of lifecycle operations. It never evaluates shell from configuration. Role-specific behavior is implemented behind a small runner interface so command construction can be unit tested without invoking package managers or system services.

The project uses these names consistently:

- CLI: `robot-agent`
- Repository and installation directory: `tangying-robot-agent-os`
- Cloud service: `tangying-robot-cloud`
- Laptop service: `tangying-robot-local-agent`
- Raspberry Pi services: `tangying-robot-edge`, `tangying-robot-safety`, and `tangying-xlerobot`

## 5. Filesystem and Configuration Contract

### macOS laptop

```text
~/Library/Application Support/TangyingRobotAgent/
  install.json
  config.env
  agent.db
  certs/
  logs/
```

### Linux cloud, laptop, and Raspberry Pi

```text
/opt/tangying-robot-agent-os/        versioned checkout or release
/etc/tangying-robot-agent-os/        non-secret configuration
/var/lib/tangying-robot-agent-os/    databases, calibration, receipts, certificates
/var/log/tangying-robot-agent-os/    service logs when not using journald
```

Configuration is generated from committed examples. Secrets use mode `0600`; private-key directories use `0700`. Installation receipts contain role, version, commit, platform, architecture, install time, and enabled services, but never contain private keys or tokens.

## 6. Role Behavior

### 6.1 Simulation

The simulation role installs Go, Python 3.11, Protocol Buffers, and project dependencies when missing. It builds the cloud and Local Agent binaries, creates the Python environment, and exposes `robot-agent demo`.

`robot-agent demo` starts the seeded MuJoCo gateway, development cloud, and Local Agent on loopback addresses, submits the Chinese example request, approves it, waits for `SUCCEEDED`, prints the task URL and trace, and shuts down transient processes on exit. Plaintext gRPC is enabled only inside this explicit simulation path.

### 6.2 Cloud

The cloud role installs Docker Engine and the Compose plugin from the operating system's supported package path when missing. It writes a deployment `.env`, binds the operator API to `127.0.0.1:8080` by default, starts PostgreSQL and the cloud control plane, and waits for `/healthz`.

Public Internet exposure is not automatic. The user must provide HTTPS and authentication through a trusted reverse proxy or private network before changing the bind address. Redis is not started because the current control plane does not depend on it.

### 6.3 Local laptop

The local role builds `robot-agent` and `local-agent`, creates persistent directories, installs a launchd unit on macOS or a systemd user unit on Ubuntu, and leaves the service disabled until configuration is valid. `robot-agent configure local` records the cloud URL, Robot Gateway address, agent ID, and certificate paths.

`robot-agent doctor --role local` checks DNS, cloud health, Robot Gateway TCP reachability, certificate expiry and permissions, local database writability, and installed binary versions. It does not move the robot.

### 6.4 Raspberry Pi Robot Edge

The Pi role verifies Ubuntu 24.04 arm64, installs ROS 2 Jazzy from the official ROS apt repository, initializes rosdep, installs workspace dependencies, builds the four ROS packages, and installs systemd services. ROS discovery remains localhost-only.

The installer clones XLeRobot at the adapter's pinned upstream commit under `/opt/XLeRobot`, then installs the matching LeRobot integration in an isolated Python environment. It creates persistent udev rules for the two USB motor control boards when stable serial attributes are available; otherwise it stops and tells the user how to identify the boards. It never substitutes world-writable `chmod 666` as a persistent permission strategy.

The Pi service remains stopped until all of these gates pass:

- two configured serial devices exist;
- XLeRobot/LeRobot imports succeed;
- calibration exists;
- Robot Gateway mTLS material exists;
- the operator confirms the physical emergency stop checklist.

## 7. Laptop-to-Pi Pairing

The first release uses a single-owner local certificate authority on the laptop. Pairing is one command after SSH access works:

```bash
robot-agent pair xlerobot.local --ssh-user tangying-robot
```

The command:

1. verifies the Pi host key through normal SSH behavior;
2. creates or reuses the laptop-local CA;
3. issues a client certificate for the Local Agent;
4. issues a server certificate with the robot hostname and resolved IP subject alternative names;
5. copies only the server key/certificate and client CA certificate to the Pi;
6. installs files with restrictive ownership and permissions;
7. restarts the Robot Gateway and verifies mTLS capabilities;
8. records the Robot Gateway address in the laptop configuration.

The CA private key never leaves the laptop. Pairing refuses noninteractive host-key acceptance. Re-pairing rotates leaf certificates but preserves the CA unless `--new-ca` is explicitly requested.

## 8. XLeRobot Hardware Boundary

There is no STM32 installer. The hardware path is:

```text
Local Agent
  mTLS gRPC
Raspberry Pi Robot Gateway
  local ROS 2 action
XLeRobot LeRobot adapter
  USB serial
two motor control boards
  Feetech serial bus, 12 V power
STS3215 servos
```

Software installation can automate packages, services, device detection, and diagnostics. It cannot safely automate mechanical assembly, servo ID assignment, 12 V wiring, calibration, physical emergency-stop installation, or the first motion test. README instructions must separate powered-off wiring steps from powered motion steps and link the mandatory safety checklist before any command that enables torque.

## 9. README Information Architecture

The README is rewritten in this order:

1. What the system does and the three software endpoints.
2. Five-minute simulation quickstart.
3. Deployment chooser and support matrix.
4. Cloud installation and health check.
5. Laptop Local Agent installation.
6. Raspberry Pi/XLeRobot installation.
7. Laptop-to-Pi pairing.
8. First safe hardware preflight without motion.
9. First approved tabletop task.
10. Daily operation through `robot-agent`.
11. Troubleshooting by endpoint.
12. Security, safety, development, and upgrade instructions.

Each command block states which machine it runs on. Expected success output follows commands that otherwise leave the user unsure. The README links endpoint-specific runbooks for deeper recovery details.

## 10. Failure Handling and Rollback

- Preflight failures make no machine changes.
- Package installation failures retain a receipt with the failed stage so reruns resume safely.
- Existing config is copied to a timestamped backup before migration.
- Service startup uses bounded health-check timeouts and prints the relevant log command on failure.
- `robot-agent stop` is always available even when configuration validation fails.
- `robot-agent uninstall` is intentionally excluded from this increment because deleting databases, certificates, ROS workspaces, or calibration requires a separate destructive-action design.
- Upgrades install a new checkout, run verification, and switch services only after success; the previous checkout remains available for rollback.

## 11. Verification Strategy

Automated tests cover:

- supported and unsupported OS/architecture detection;
- idempotent receipt and configuration generation;
- installer dry-run command plans for every role;
- refusal to overwrite secrets or accept unsafe permissions;
- `robot-agent` lifecycle command allowlists;
- certificate SANs and file permissions in a temporary SSH-free pairing fixture;
- README command references resolving to real scripts and flags;
- Docker Compose configuration validation;
- simulation installation and `robot-agent demo` in CI;
- ROS 2 workspace build in the existing Jazzy container job.

Fresh-environment smoke tests run in Ubuntu and Debian containers. macOS CI validates the local installer in dry-run mode and runs Go tests; system launch is verified manually because GitHub-hosted macOS jobs cannot behave exactly like a logged-in user launchd session. Raspberry Pi arm64 is covered by the Jazzy arm64-compatible container build plus a documented physical Pi checklist.

## 12. Release Boundary

This work produces the next release candidate after all local and GitHub checks pass. It may claim one-command software installation on the supported platforms and simulation completion. It must not claim unattended hardware commissioning or stable physical manipulation until the emergency-stop and 30-trial XLeRobot hardware gates pass.
