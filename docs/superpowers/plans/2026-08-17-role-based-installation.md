# Role-Based Installation and Quickstart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an idempotent `install.sh` and `robot-agent` command that install, diagnose, start, pair, and operate the simulation, cloud, laptop, and Raspberry Pi roles from a fresh supported environment, with a README that guides a first-time user end to end.

**Architecture:** A strict Bash bootstrap layer detects the platform and prepares role-specific dependencies, while a small Go CLI exposes safe lifecycle commands and dispatches only committed scripts or service operations. Machine-readable receipts connect installation state to the CLI; mTLS pairing is implemented as an auditable OpenSSL/SSH workflow; hardware motion remains outside installation.

**Tech Stack:** Bash, Go 1.26, Python 3.11+, pytest, Docker Compose, OpenSSL, launchd, systemd, ROS 2 Jazzy, GitHub Actions.

---

## File map

- `install.sh`: public bootstrap entry point and role/flag parser.
- `scripts/install/common.sh`: platform detection, strict command runner, confirmations, versions, receipts, and shared dependency helpers.
- `scripts/install/sim.sh`: simulation prerequisites, project build, and receipt.
- `scripts/install/cloud.sh`: Docker/Compose installation, deployment configuration, startup, and health check.
- `scripts/install/local.sh`: laptop binaries, persistent directories, launchd/systemd user service, and receipt.
- `scripts/install/robot-pi.sh`: Ubuntu arm64/ROS 2/XLeRobot install, colcon build, systemd, and hardware preflight.
- `scripts/demo.sh`: bounded full-process simulation demo.
- `scripts/pair-robot.sh`: local CA, leaf certificates, SSH deployment, and mTLS verification.
- `internal/robotagent/app.go`: CLI parsing, installation receipt, command allowlist, and role lifecycle plans.
- `cmd/robot-agent/main.go`: production CLI entry point.
- `deploy/config/*.env.example`: role configuration examples.
- `deploy/laptop/*.service`: Linux user service plus existing macOS launchd definition.
- `deploy/raspberry-pi/*.service`: final XLeRobot and edge service definitions.
- `tests/install/`: bootstrap, role, pairing, README, and clean-environment contract tests.
- `docs/install/`: endpoint-specific installation and troubleshooting runbooks.
- `README.md`: deployment chooser and first-run guide.

## Task 1: Lock the bootstrap and documentation contract

**Files:**
- Create: `tests/install/test_bootstrap_contract.py`
- Create: `tests/install/test_readme_contract.py`
- Create: `tests/install/__init__.py`

- [ ] **Step 1: Write failing bootstrap tests**

Tests execute `bash install.sh --help`, assert the four exact roles, run every role with `--dry-run` and test-only platform overrides, and assert unsupported platforms fail before emitting a mutation command.

- [ ] **Step 2: Write the failing README contract test**

The test extracts referenced local scripts and `robot-agent` subcommands from README, then asserts scripts exist and the CLI help advertises every documented command.

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest tests/install/test_bootstrap_contract.py tests/install/test_readme_contract.py -q
```

Expected: failure because `install.sh`, role scripts, and `robot-agent` do not exist.

- [ ] **Step 4: Commit contract tests**

```bash
git add tests/install
git commit -m "test: define role-based installation contract"
```

## Task 2: Implement the safe bootstrap core

**Files:**
- Create: `install.sh`
- Create: `scripts/install/common.sh`
- Create: `scripts/install/sim.sh`
- Create: `scripts/install/cloud.sh`
- Create: `scripts/install/local.sh`
- Create: `scripts/install/robot-pi.sh`
- Create: `deploy/config/cloud.env.example`
- Create: `deploy/config/local.env.example`
- Create: `deploy/config/robot-pi.env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Implement strict root argument parsing**

Accept one role plus `--yes`, `--dry-run`, `--version`, and `--help`; reject unknown roles and flags. Export normalized options and source only the selected committed role script.

- [ ] **Step 2: Implement shared platform and mutation controls**

Detect macOS and `/etc/os-release`, normalize `amd64`/`arm64`, permit platform overrides only when `ROBOT_AGENT_TEST_MODE=1`, quote dry-run output, and require confirmation before privileged package installation unless `--yes` is present.

- [ ] **Step 3: Implement receipts and safe configuration creation**

Write role, version, commit, OS, architecture, and timestamp atomically. Create configuration from examples with mode `0600`, back up existing config before migration, and never include credentials in receipts.

- [ ] **Step 4: Implement role dry-run plans and real setup functions**

Simulation installs build dependencies and creates the Python environment; cloud installs Docker and starts Compose; local builds binaries and installs launchd/systemd user definitions; robot-pi verifies Ubuntu 24.04 arm64, installs ROS 2 Jazzy, clones pinned XLeRobot, builds colcon, and installs services.

- [ ] **Step 5: Run bootstrap tests and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/install/test_bootstrap_contract.py -q
bash -n install.sh scripts/install/*.sh
```

Expected: all pass.

- [ ] **Step 6: Commit bootstrap implementation**

```bash
git add install.sh scripts/install deploy/config .gitignore
git commit -m "feat: add role-based installation bootstrap"
```

## Task 3: Implement the `robot-agent` CLI

**Files:**
- Create: `internal/robotagent/app.go`
- Create: `internal/robotagent/app_test.go`
- Create: `cmd/robot-agent/main.go`

- [ ] **Step 1: Write failing Go tests**

Tests cover help, version, receipt role selection, explicit role override, start/stop/restart/status/log command plans, rejection of unknown roles and commands, and `stop` availability with an invalid config.

- [ ] **Step 2: Run tests and verify RED**

Run: `go test ./internal/robotagent ./cmd/robot-agent`

Expected: failure because packages do not exist.

- [ ] **Step 3: Implement the CLI app**

Use `flag.FlagSet`, `encoding/json`, and `exec.CommandContext`. Define a `Runner` interface so tests capture exact executable/arguments. Read receipts from the platform state directory or `ROBOT_AGENT_STATE_DIR`. Dispatch a fixed command plan per role; never run configuration text through a shell.

- [ ] **Step 4: Add doctor and configure behavior**

Doctor checks expected binaries, files, directory permissions, HTTP health, TCP reachability, and certificate expiry without hardware motion. Configure writes only known keys to the role config using atomic replacement.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `go test ./internal/robotagent ./cmd/robot-agent`

Expected: pass.

- [ ] **Step 6: Commit the CLI**

```bash
git add internal/robotagent cmd/robot-agent
git commit -m "feat: add robot-agent lifecycle CLI"
```

## Task 4: Add bounded simulation demo and cloud deployment defaults

**Files:**
- Create: `scripts/demo.sh`
- Create: `tests/install/test_demo_contract.py`
- Modify: `deploy/docker-compose.yml`
- Modify: `Makefile`

- [ ] **Step 1: Write failing demo and Compose tests**

Assert the demo uses loopback ports, registers cleanup traps, accepts a seed, invokes the real cloud/Local Agent/MuJoCo processes, and has a successful `--check` mode. Assert Compose binds cloud/PostgreSQL to loopback by default and contains no unused Redis service.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/install/test_demo_contract.py -q`

Expected: failure because the demo is missing and Compose exposes unnecessary services.

- [ ] **Step 3: Implement demo orchestration**

Add port availability checks, temporary state, process cleanup, task submission/approval, Local Agent execution, terminal-state assertion, and concise success output. `--check` validates dependencies without starting processes.

- [ ] **Step 4: Harden Compose defaults**

Parameterize bind addresses with loopback defaults, remove Redis, retain PostgreSQL health checks, and add `make install-check` plus `make demo` targets.

- [ ] **Step 5: Run tests and E2E**

Run:

```bash
.venv/bin/pytest tests/install/test_demo_contract.py tests/e2e -q
bash scripts/demo.sh --check
```

Expected: pass.

- [ ] **Step 6: Commit demo and cloud changes**

```bash
git add scripts/demo.sh tests/install/test_demo_contract.py deploy/docker-compose.yml Makefile
git commit -m "feat: add one-command simulation demo"
```

## Task 5: Implement laptop-to-Pi mTLS pairing

**Files:**
- Create: `scripts/pair-robot.sh`
- Create: `tests/install/test_pairing.py`
- Modify: `internal/robotagent/app.go`
- Modify: `internal/robotagent/app_test.go`

- [ ] **Step 1: Write failing certificate tests**

Use a temporary local transport fixture to assert CA key remains local, server certificate includes hostname and IP SANs, client certificate is issued, remote files exclude the CA key, file permissions are restrictive, and re-pairing preserves the CA.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/install/test_pairing.py -q`

Expected: failure because the pairing script does not exist.

- [ ] **Step 3: Implement pairing**

Validate SSH tools and OpenSSL, require normal SSH host-key verification, generate EC P-256 CA/leaf certificates, deploy with `scp` plus an allowlisted remote installation command, restart the edge service, and verify the gRPC TCP/TLS endpoint. Provide `ROBOT_AGENT_PAIR_LOCAL_ROOT` only in explicit test mode for the SSH-free fixture.

- [ ] **Step 4: Wire `robot-agent pair`**

Resolve the installed script path, pass hostname and SSH user as separate arguments, and test command construction.

- [ ] **Step 5: Run pairing and Go tests**

Run:

```bash
.venv/bin/pytest tests/install/test_pairing.py -q
go test ./internal/robotagent ./cmd/robot-agent
```

Expected: pass.

- [ ] **Step 6: Commit pairing**

```bash
git add scripts/pair-robot.sh tests/install/test_pairing.py internal/robotagent
git commit -m "feat: add secure laptop robot pairing"
```

## Task 6: Finalize laptop and Raspberry Pi service deployment

**Files:**
- Create: `deploy/laptop/tangying-robot-local-agent.service`
- Create: `deploy/raspberry-pi/99-tangying-xlerobot.rules`
- Create: `deploy/raspberry-pi/tangying-robot-edge.service`
- Create: `deploy/raspberry-pi/tangying-xlerobot.service`
- Delete: `deploy/raspberry-pi/tangying-robot-gateway.service`
- Delete: `deploy/raspberry-pi/tangying-ros.service`
- Modify: `deploy/laptop/com.tangying.robot-agent.plist`
- Modify: `tests/deploy/test_deployment_contract.py`

- [ ] **Step 1: Extend failing deployment contract tests**

Assert consistent service names, localhost ROS discovery, mTLS paths, no placeholder cloud domain, dialout device permissions through udev, edge dependency ordering, and Safety Supervisor launch.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/deploy/test_deployment_contract.py -q`

Expected: failure against the old service names/templates.

- [ ] **Step 3: Implement final service templates**

Add hardened systemd settings, restart policies, explicit environment/config files, launchd arguments, and udev group access without `chmod 666`. Keep ROS DDS on localhost.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.venv/bin/pytest tests/deploy/test_deployment_contract.py -q`

Expected: pass.

- [ ] **Step 5: Commit deployment services**

```bash
git add deploy tests/deploy/test_deployment_contract.py
git commit -m "feat: harden laptop and robot edge services"
```

## Task 7: Rewrite README and endpoint runbooks

**Files:**
- Rewrite: `README.md`
- Rewrite: `docs/quickstart.md`
- Create: `docs/install/cloud.md`
- Create: `docs/install/local.md`
- Create: `docs/install/robot-pi.md`
- Create: `docs/install/troubleshooting.md`
- Modify: `docs/xlerobot-setup.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Rewrite README in the approved order**

Put the five-minute simulation first, label every command by machine, include expected output, deployment chooser, complete cloud/local/Pi flow, pairing, no-motion preflight, first approved task, daily commands, support matrix, and safety boundary.

- [ ] **Step 2: Add endpoint runbooks**

Document prerequisites, install/start/status/log/upgrade/recovery commands for every endpoint. Explain STS3215, motor control boards, power-off wiring, serial IDs, calibration, and why no STM32 installation exists.

- [ ] **Step 3: Run documentation contract tests**

Run: `.venv/bin/pytest tests/install/test_readme_contract.py -q`

Expected: pass with every referenced local path and CLI command present.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md docs CHANGELOG.md
git commit -m "docs: add complete robot installation quickstart"
```

## Task 8: Add fresh-environment CI and complete release verification

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_repository.py`

- [ ] **Step 1: Add CI dry-run matrix**

Run bootstrap dry-runs for Ubuntu cloud/local/sim, Debian cloud, macOS local/sim, and Ubuntu arm64 Pi platform detection. Keep the real Jazzy colcon build job.

- [ ] **Step 2: Run the local verification matrix**

Run:

```bash
make generate-check
make lint
make test
make install-check
make e2e
.venv/bin/python scripts/run_simulation_acceptance.py --episodes 30 --seed 20260817
docker compose -f deploy/docker-compose.yml config
docker run --rm -v "$PWD/robot/ros2_ws:/ws" -w /ws ros:jazzy-ros-base bash -lc '. /opt/ros/jazzy/setup.sh && colcon build --event-handlers console_cohesion+'
git diff --check
```

Expected: every command succeeds; simulation remains 30/30 with zero safety violations; four ROS packages build.

- [ ] **Step 3: Commit CI changes**

```bash
git add .github/workflows/ci.yml tests/test_repository.py
git commit -m "ci: verify role installers on clean platforms"
```

- [ ] **Step 4: Push and monitor CI**

```bash
git push origin codex/v0.1
gh run watch --exit-status
```

Expected: test, installer matrix, and ROS build jobs are green.

- [ ] **Step 5: Publish the next release candidate**

Tag the verified commit as `v0.1.0-rc.2`, push the tag, and wait for tag CI. Do not create `v0.1.0` until physical hardware acceptance passes.
