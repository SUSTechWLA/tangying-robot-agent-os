# First-Run Setup & Pairing Audit

**Scope:** "a user buys a robot, connects it to the network, and immediately uses it via natural language" — zero-touch / hot-plug onboarding.
**Method:** read-only static inspection of the working tree (no files modified). Every claim below is anchored to `file:line`.
**Verdict (summary):** the repository contains a **carefully engineered, deliberately gated, operator-driven commissioning path**. It is not a consumer onboarding path. There is **no robot discovery of any kind**, no pairing code, no announcement, no auto-start, and the natural-language loop cannot execute on real hardware until files edited by hand, hardware calibrated, and torque armed locally. Details and evidence follow.

---

## (A) Exact sequence a human must perform today

From "robot in the box" to "robot executing a natural-language task". Commands are quoted as they appear in the repository.

### Phase 0 — Robot hardware and OS (not automated by this repo)

1. **Assemble the robot, wire the two USB servo boards, wire 12 V, install an E-stop.** Explicitly out of scope for the installer — `docs/install/robot-pi.md:13`: *"舵机 ID、机械零位、12 V 极性、总线接线和实体急停属于硬件装配，不能被一键安装安全替代"*.
2. **Install Ubuntu Server 24.04 arm64 on a Raspberry Pi 4/5.** Only supported robot platform — `scripts/install/common.sh:128` (`robot-pi:linux:ubuntu:24.04:arm64`).
3. **Ensure the Pi is reachable by SSH with a login account.** `docs/install/robot-pi-quick.md:32-33` uses `ssh ubuntu@xlerobot.local` and warns that the service account `tangying-robot` should *not* be assumed to have SSH credentials (`docs/install/robot-pi-quick.md:38`).
4. **Give the Pi a name/address the laptop can resolve.** The entire documented flow assumes a working hostname (canonically `xlerobot.local`). Nothing in this repository configures the Pi's hostname, mDNS responder, or DHCP reservation. *(Claim "nothing configures it": I searched `install.sh`, `scripts/install/*`, and `deploy/robot/*` for hostname/avahi/mDNS configuration and found none — see §5.)*

### Phase 1 — Clone (both machines)

5. `git clone --branch v0.3.0 https://github.com/SUSTechWLA/tangying-robot-agent-os.git` and `cd tangying-robot-agent-os` — on the **robot** (`docs/install/robot-pi.md:23-24`) and on the **laptop** (`docs/operations/fresh-deployment.md:12-13`).

### Phase 2 — Robot-side install

6. `./scripts/precheck.sh robot-pi` — read-only cold-start check (`docs/operations/fresh-deployment.md:14`, `docs/operations/fresh-deployment.md:132-134`; `scripts/precheck.sh` header lines 1-23).
7. `./install.sh robot-pi --dry-run --yes` then `./install.sh robot-pi --yes` (`docs/install/robot-pi.md:26-27`, `README.md:196-197`).
   - `ROBOT_AGENT_ROLE=robot-pi` dispatches to `scripts/install/robot-pi.sh` (`install.sh:91`).
   - What it does: `ensure_go` (`scripts/install/robot-pi.sh:95`), create system user `tangying-robot` (`scripts/install/robot-pi.sh:29`), `git clone` XLeRobot pinned to commit `3d14695e…` into `/opt/XLeRobot` (`scripts/install/robot-pi.sh:33-43`, `scripts/install/common.sh:9`), tar-export the repo to `/opt/tangying-robot-agent-os` (`scripts/install/robot-pi.sh:98` → `scripts/install/common.sh:315-332`), build+install the `robot-agent` CLI to `/usr/local/bin/robot-agent` (`scripts/install/robot-pi.sh:99` → `scripts/install/common.sh:334-339`), create a venv and `pip install -e /opt/tangying-robot-agent-os[robot-pi]` (`scripts/install/robot-pi.sh:58-65`), copy the pinned XLeRobot `xlerobot_2wheels` and `model` modules into LeRobot's package dir (`scripts/install/robot-pi.sh:68-69`), install udev rules + systemd units (`scripts/install/robot-pi.sh:79-82`), install `/etc/tangying-robot-agent-os/robot-pi.env` from the example (`scripts/install/robot-pi.sh:104`), create `/var/lib/tangying-robot-agent-os/certs` and `…/calibration` (`scripts/install/robot-pi.sh:110-111`), then `write_receipt` (`scripts/install/robot-pi.sh:131`).
   - Note `direct_edge()` returns `0` unconditionally (`scripts/install/robot-pi.sh:84-86`), so the ROS 2 branch (`install_ros_jazzy`, `colcon build`) is **dead code in the current tree**.
   - Installer's own closing statement, `scripts/install/robot-pi.sh:132`: *"Robot Edge installed but **stopped** pending certificates, serial devices, calibration, and safety checklist"*.
8. `./scripts/robot-pi-preflight.sh` — offline pre-power check (`docs/operations/fresh-deployment.md:136`; `scripts/robot-pi-preflight.sh:4` defaults to `/etc/tangying-robot-agent-os/robot-pi.env`). The installer **does not** run this.

### Phase 3 — Robot-side manual hardware binding (hand-edited files)

9. **Discover the two USB serial numbers:** `udevadm info --query=property --name=/dev/ttyACM0 | grep -E 'ID_SERIAL(_SHORT)?='` (and `ttyACM1`) — `docs/install/robot-pi.md:49-50`.
10. **Hand-edit the udev rules file**, replacing two literal placeholders: `sudoedit /etc/udev/rules.d/99-tangying-xlerobot.rules` then `sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=tty` — `docs/install/robot-pi.md:57-59`. The shipped rule file contains the literals `XLEROBOT_LEFT_SERIAL` and `XLEROBOT_RIGHT_SERIAL` and its own header says *"Rules remain inert until the operator records the physical left/right mapping"* — `deploy/robot/raspberry-pi/99-tangying-xlerobot.rules:1-5`. **This is a mandatory hand-edit of a root-owned file; the installer never fills it in.**
11. **Calibrate the robot, with torque enabled and a human moving joints:**
    `sudo -u tangying-robot /opt/tangying-robot-agent-os/.venv/bin/python /opt/tangying-robot-agent-os/scripts/calibrate_xlerobot.py --acknowledge-hardware-motion` — `docs/install/robot-pi.md:77-79`. Output must land at `/var/lib/tangying-robot-agent-os/calibration/tangying-xlerobot.json` (`docs/install/robot-pi.md:85`). The doc states this cannot run unattended (`docs/install/robot-pi.md:73`).

### Phase 4 — Laptop-side install

12. `./scripts/precheck.sh local` then `./install.sh local --dry-run --yes` / `./install.sh local --yes` (`docs/install/local.md:15-16`, `docs/operations/fresh-deployment.md:107-109`).
    - macOS branch installs Homebrew if absent (`scripts/install/common.sh:247-254`), `brew install go` (`scripts/install/local.sh:38`), builds `bin/robot-agent` + `bin/local-agent` (`scripts/install/common.sh:308-313`), copies both to `/Users/Shared/TangyingRobotAgent/bin/` (`scripts/install/local.sh:7-8`), renders `__HOME__` into `com.tangying.robot-agent.plist` and installs it to `~/Library/LaunchAgents/` (`scripts/install/local.sh:14-17`), and installs `local.env` from the example (`scripts/install/local.sh:48`).
    - Linux branch `apt-get install ca-certificates curl git openssl` + Go (`scripts/install/local.sh:40-42`), installs a systemd **user** unit (`scripts/install/local.sh:22-24`).
    - Closing statement, `scripts/install/local.sh:52`: *"Local Agent installed but **not started**"*.
13. `robot-agent configure local` — prints the config path only when no `KEY=VALUE` is given (`internal/robotagent/app.go:299-302`). `robot-agent doctor local` (`docs/install/local.md:17-18`).

### Phase 5 — Pairing (laptop → robot, over SSH)

14. **Human must already know the robot's hostname or IP.** Default SSH user in the CLI is `ubuntu` (`internal/robotagent/app.go:265`).
15. `ssh ubuntu@xlerobot.local` — man-in-the-loop host-key confirmation is required and documented as such: *"首次 SSH 必须人工核对主机指纹"* (`docs/install/local.md:28,33`; `docs/sim2real/README.md:90`).
16. `robot-agent pair xlerobot.local --ssh-user ubuntu` (`docs/install/local.md:29`, `docs/install/robot-pi-quick.md:34`, `docs/sim2real/README.md:91`). Implementation: `cmd/robot-agent/main.go:14-18` → `internal/robotagent/app.go:75-76` → `app.go:260-286` → `bash <root>/scripts/pair-robot.sh <host> --ssh-user <user>` (`internal/robotagent/app.go:281`).
17. `robot-agent doctor local` again (`docs/install/local.md:30`).

### Phase 6 — Robot-side pre-flight and start (back on the Pi)

18. `sudo robot-agent doctor robot-pi` (`docs/install/robot-pi.md:96`; `docs/install/robot-pi-quick.md:42`).
19. `sudo robot-agent start robot-pi` (`docs/install/robot-pi.md:98`; `docs/install/robot-pi-quick.md:44`) → `systemctl start tangying-robot-edge.service` (`internal/robotagent/app.go:206`, `app.go:219-233`).
    - **The unit is never `enable`d.** `scripts/install/robot-pi.sh:72-82` only writes the unit and runs `systemctl daemon-reload`; the only `systemctl` call in `pair-robot.sh` is `try-restart` (`scripts/pair-robot.sh:159`), which is a no-op on a unit that is not running. The units do declare `WantedBy=multi-user.target` (`deploy/robot/raspberry-pi/tangying-robot-edge-direct.service:29-30`), so a human could enable them, but nothing in the repository does. I searched the whole tree for `systemctl enable`, `enable-linger`, and `launchctl bootstrap` outside of the CLI lifecycle command and found none.
20. `sudo robot-agent status robot-pi` / `sudo robot-agent logs robot-pi --follow` (`docs/install/robot-pi.md:99-100`).

### Phase 7 — Laptop-side start

21. `robot-agent start local` (`docs/install/local.md:38`) → on macOS `launchctl bootstrap gui/$UID <plist>` (`internal/robotagent/app.go:240-241`), on Linux `systemctl --user start tangying-robot-local-agent.service` (`app.go:203`, `app.go:219-233`).
22. **Open `http://127.0.0.1:8787/`** (`docs/install/local.md:21`; default listen address `127.0.0.1:8787` at `cmd/local-agent/main.go:71`).
23. *(Optional)* Configure the LLM in the console at 开发模式 → 开发诊断: Base URL / model / API key (`docs/install/local.md:21`, `README.md:76-83`). Without it `AGENT_PROVIDER=deterministic` and only the built-in phrasings parse (`cmd/local-agent/main.go:80`; `README.md:72-74`).

### Phase 8 — Provisioning work that stands between "connected" and "executes a task"

24. **Write a perception provider and a verification provider, and point the robot at them.** `robot-pi.env.example` ships both commented out (`deploy/robot/raspberry-pi/robot-pi.env.example:13-15`). The backend requires them for anything physical: `physical_ready = driver_ready and entity_ready and verify_ready` (`robot/gateway/tangying_robot_gateway/xlerobot_backend.py:131-137`), producing blockers `ENTITY_PROVIDER_REQUIRED` and `VERIFIER_REQUIRED` (`xlerobot_backend.py:135-137`). The interface the operator must implement is specified only as prose and a Python callable path (`docs/sim2real/README.md:118-127`).
25. **Stop the systemd service and arm the robot by hand, in an interactive local terminal on the Pi:**
    ```
    sudo robot-agent stop robot-pi
    sudo -u tangying-robot bash
    cd /opt/tangying-robot-agent-os
    set -a; source /etc/tangying-robot-agent-os/robot-pi.env; set +a
    export PYTHONPATH="$PWD/python:$PWD/robot/gateway:$PWD/robot/ros2_ws/src/xlerobot_adapter"
    .venv/bin/python -m tangying_robot_gateway.run_direct_edge --connect --arm --operator-present
    ```
    — `docs/sim2real/README.md:147-156`. The doc is explicit: *"不要把 `--arm` 写入开机服务"* (`docs/sim2real/README.md:158`). Enforcement is in code: `--arm` requires `--connect`, `--operator-present`, **and** an interactive TTY (`robot/gateway/tangying_robot_gateway/run_direct_edge.py:44-46`, using `sys.stdin.isatty()` at `run_direct_edge.py:173`), and refuses if either provider is missing (`run_direct_edge.py:47-48`).
    There is **no network-reachable arming RPC**: the service contract has only `GetRuntimeInfo`, `Observe`, `ExecuteSkill`, `Cancel`, `EmergencyStop`, `ListServices`, `CallService` (`proto/robot/v1/robot.proto:9-19`).
26. *(Fleet route only)* Start a separately-built policy sidecar and the edge worker, and hand-copy the pairing paths into a different config file: *"把配对得到的 `ROBOT_CA / ROBOT_CERT / ROBOT_KEY / ROBOT_SERVER_NAME` 对应填入 `edge.env` 的 `EDGE_RUNTIME_CA / CERT / KEY / SERVER_NAME`"* (`docs/sim2real/README.md:96`, `docs/sim2real/README.md:160-169`).
27. **Only then** type the natural-language task into the 工作台 (`docs/sim2real/README.md:171`; `README.md:52`).

**Count:** ~27 discrete human actions, of which **at least 8 are mandatory hand-edits or interactive local-terminal operations on physical hardware** (steps 4, 9, 10, 11, 14/15, 24, 25), across two machines, three shells, and one root-owned udev file.

---

## (B) Every place the flow requires a human to know or type something

| # | What the human must know / type | Where it is required | Evidence |
|---|---|---|---|
| B1 | **Robot hostname or IP** | `pair` argument | `scripts/pair-robot.sh:16` (`ROBOT_HOST=$1`); `internal/robotagent/app.go:262` (`pair requires ROBOT_HOST`); `internal/robotagent/app.go:98` |
| B2 | **SSH user name** | `--ssh-user`; defaults to `ubuntu` | `scripts/pair-robot.sh:18`; `internal/robotagent/app.go:265` |
| B3 | **The robot must actually resolve** from the laptop | `getent ahostsv4` / `dscacheutil` / `socket.gethostbyname` | `scripts/pair-robot.sh:73-85` |
| B4 | **That the robot is the robot** (no verification) | successful SSH host-key confirmation | `docs/install/local.md:33`; `docs/sim2real/README.md:90` |
| B5 | **Account must have working `sudo` on the Pi** | install certs into `/var/lib/...` and restart the unit | `scripts/pair-robot.sh:159` (`sudo install …`, `sudo systemctl try-restart`) |
| B6 | **Two USB serial numbers**, transcribed by hand | udev rule placeholders | `deploy/robot/raspberry-pi/99-tangying-xlerobot.rules:4-5`; `docs/install/robot-pi.md:49-59` |
| B7 | **Which board is physical left vs right** | udev rule mapping / port config | `deploy/robot/raspberry-pi/99-tangying-xlerobot.rules:3`; `docs/install/robot-pi.md:53`; `docs/sim2real/README.md:85` |
| B8 | **Robot config file path** `/etc/tangying-robot-agent-os/robot-pi.env` | manual edits / `configure robot-pi` | `scripts/xlerobot_preflight.py:45`; `docs/sim2real/README.md:152` |
| B9 | **Laptop config file path** `local.env` | `--config`, or the service unit's hardcoded `%h/.config/tangying-robot-agent-os/local.env` | `deploy/local/tangying-robot-local-agent.service:8`; `deploy/local/com.tangying.robot-agent.plist:9` |
| B10 | **Two absolute certificate paths + key path**, copied into a *third* config file for the Fleet route | `edge.env` `EDGE_RUNTIME_CA/CERT/KEY/SERVER_NAME` | `docs/sim2real/README.md:96` |
| B11 | **Port 50051**, when typing an address explicitly | `ROBOT_ADDRESS=host:50051` | `deploy/local/local.env.example:2`; `deploy/robot/raspberry-pi/robot-pi.env.example:1`; `cmd/local-agent/main.go:72` |
| B12 | **A robot identity string** (or accept a shared default) | `ROBOT_ID` | `robot/gateway/tangying_robot_gateway/xlerobot_backend.py:122` — `os.getenv("ROBOT_ID", "xlerobot-edge-direct")`. Note `ROBOT_ID` is **absent from `robot-pi.env.example`**, so out of the box every robot reports the same ID. |
| B13 | **Two Python callable paths** for perception/verification | `ROBOT_ENTITY_PROVIDER` / `ROBOT_VERIFIER_PROVIDER` | `deploy/robot/raspberry-pi/robot-pi.env.example:13-15`; `docs/sim2real/README.md:121-122` |
| B14 | **Recognized calibration file path** | must be exactly `…/calibration/tangying-xlerobot.json` | `docs/install/robot-pi.md:85` |
| B15 | **An operator name and a written reason** to clear an E-stop | `--operator`, `--reset-reason` | `docs/sim2real/README.md:182-184`; `robot/gateway/tangying_robot_gateway/run_direct_edge.py:70-71` |
| B16 | **LLM base URL, model, API key** (optional, for free-form phrasing) | console 开发诊断, or `AGENT_*` keys | `internal/localconfig/settings.go:66-72`; `cmd/local-agent/main.go:80-83` |

---

## (C) What is automatic today

Genuinely automatic, verified:

1. **Platform detection and role validation.** `detect_platform` (`scripts/install/common.sh:83-117`) and `validate_role_platform` (`scripts/install/common.sh:119-131`) hard-fail with a precise message on unsupported combinations.
2. **Package/toolchain installation for the selected role**, including a checksum-verified Go toolchain download (`scripts/install/common.sh:256-279`, sha256 pinned at lines 261-262) and a Homebrew bootstrap on macOS (`scripts/install/common.sh:247-254`). Interactive confirmation unless `--yes` (`scripts/install/common.sh:60-73`).
3. **`--dry-run` mutation plan printing** for every mutating operation (`scripts/install/common.sh:24-50`).
4. **An install receipt** written to `<state>/install.json` with role/version/commit/os/arch/timestamp (`scripts/install/common.sh:224-245`), which the CLI later reads to infer the role when none is given (`internal/robotagent/app.go:179-185`).
5. **Certificate generation on pairing.** A local P-256 CA if absent (`scripts/pair-robot.sh:105-109`), a `local-agent` client leaf (90 days, `clientAuth`, SAN `DNS:tangying-local-agent`) and a `server` leaf (90 days, `serverAuth`, SANs `DNS:<ROBOT_HOST>,IP:<resolved IPv4>`) — `scripts/pair-robot.sh:119-143`. Correct scoping: only `server.key`, `server.crt`, and `ca.crt` go to the robot; the CA private key stays on the laptop (`scripts/pair-robot.sh:154-160`; asserted by `tests/install/test_pairing.py:37-51`).
6. **Writing the laptop's mTLS config block.** `ROBOT_ADDRESS`, `ROBOT_SERVER_NAME`, `ROBOT_CA`, `ROBOT_CERT`, `ROBOT_KEY` are merged into `local.env` idempotently (`scripts/pair-robot.sh:168-191`).
7. **Index-based DNS resolution of the robot name** across `getent` / `dscacheutil` / Python (`scripts/pair-robot.sh:68-86`).
8. **Pairing is idempotent and repairable** in the certificate sense: re-running rotates the leaf certificates while preserving the CA (`tests/install/test_pairing.py:68-76`), and `--new-ca` explicitly backs up and rotates the trust root (`scripts/pair-robot.sh:96-103`).
9. **Config-preserving installs.** `install_config_example` never overwrites an existing config (`scripts/install/common.sh:213-216`).
10. **Installation receipts + precheck.** `scripts/precheck.sh` is read-only and reports PASS/FAIL/WARN with exit codes (`scripts/precheck.sh:11-23`).
11. **The console's readiness checklist**, once a connection exists — see §6.
12. **A no-motion default service posture.** The systemd unit starts the gateway with `--connect` only, never `--arm` (`deploy/robot/raspberry-pi/tangying-robot-edge-direct.service:17,15-16`), and `prepare_hardware` refuses to arm without an interactive TTY (`robot/gateway/tangying_robot_gateway/run_direct_edge.py:44-48`).

---

## (D) What does NOT exist that a consumer hot-plug experience would require

Each item states the specific missing capability and the search that failed to find it.

1. **No robot discovery of any kind.** No mDNS/DNS-SD, Zeroconf, SSDP/UPnP, UDP broadcast/listen, or cloud registry lookup. Exhaustive scoped grep for `mdns|zeroconf|bonjour|avahi|ssdp|beacon|hotplug|auto-pair|multicast|NSNetService|dns-sd` over `cmd/ console/ core/ agent/ agentruntime/ edge/ fleet/ internal/ middleware/ orchestration/ policy/ robot/ scripts/ sim/ skills/ web/ deploy/ docs/ README.md tools.json` (excluding `vendor/`, `node_modules/`, `XLeRobot/`, `.venv/`) returned **only false positives** (colcon build logs, `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`, unrelated prose). The single mDNS reference in the tree is an unimplemented design statement: `docs/superpowers/specs/2026-08-18-local-first-runtime-design.md:245` — *"The desktop **attempts** mDNS discovery using `_tangying-robot._tcp.local`"* — and the same document's step 1 at line 249. **No code implements it.** `docs/superpowers/specs/2026-08-18-local-first-runtime-design.md:20` likewise lists "discover or enter" as design intent only.
2. **No service announcement from the robot.** Because there is no mDNS responder, the robot is silent on the network. There is nothing for a laptop to find, so "power on → it appears" is architecturally absent, not merely unpolished.
3. **No one-action pairing.** Pairing is a 3-command, 2-machine, SSH-and-sudo procedure (A14-A17). There is no `robot-agent pair --scan`, no pairing code, no QR, no button-press window, no short-lived token.
4. **No factory-provisioned identity.** The robot ships with no certificate and no key material; `install.sh robot-pi` only creates the empty directory `…/certs` (`scripts/install/robot-pi.sh:110`) and the installer explicitly stops short (*"stopped pending certificates"*, `scripts/install/robot-pi.sh:132`). There is no enrollment credential of any kind on the robot.
5. **No auto-start on boot.** Neither service is ever enabled: robot-side only `systemctl daemon-reload` (`scripts/install/robot-pi.sh:80`), laptop-side only `systemctl --user daemon-reload` (`scripts/install/local.sh:24`); pairing uses `try-restart` which cannot start a stopped unit (`scripts/pair-robot.sh:159`). A consumer who powers the robot off and on gets nothing.
6. **No console UI to add or discover a device.** The only settings surface is the LLM: routes are `GET/PUT /v1/config/llm` and `GET /v1/config/status` (`console/server.go:125-126`), and `ConfigStatus` carries only `Provider/BaseURL/Model/HasAPIKey/RestartRequired` (`console/server.go:32-38`). The robot address, certificates, and safety profile are **process-startup-only** flags/environment (`cmd/local-agent/main.go:72-79`) — changing them means editing a file and restarting the process. There is no `#devices` entry form; the page is described as read-only status (*"查看连接状态和机器人看到的现场"*, `web/console_ui.js:5`).
7. **No post-pairing self-test.** After `pair` returns, nothing verifies that the laptop can actually complete a gRPC call to the robot. `pair-robot.sh` validates only that DNS resolved (`scripts/pair-robot.sh:88-92`) — it never opens port 50051. **Consequence: pairing reports `pairing complete` even if the network path is blocked.** The operator must know to run `robot-agent doctor local` afterwards (`docs/install/local.md:30`).
8. **No independent robot identity.** Every robot that does not set `ROBOT_ID` reports the same constant `"xlerobot-edge-direct"` (`robot/gateway/tangying_robot_gateway/xlerobot_backend.py:122`), and `ROBOT_ID` is not in the env template at all (`deploy/robot/raspberry-pi/robot-pi.env.example`). Two robots in one home share an identity string.
9. **No automatic serial-device binding.** The shipped udev rules are inert placeholders requiring a human to read USB serial numbers and edit a root-owned file (`deploy/robot/raspberry-pi/99-tangying-xlerobot.rules:1-5`). Hot-plugging a servo board into a different USB port is therefore a support incident, not a non-event.
10. **No automatic calibration.** Calibration is interactive, requires torque enabled, a human moving joints, and `--acknowledge-hardware-motion` (`docs/install/robot-pi.md:71-80`). Unavoidable in principle for this hardware class — but it means the consumer experience cannot be "immediately uses it".
11. **No zero-touch perception/verification.** The robot ships with `ENTITY_PROVIDER_REQUIRED` and `VERIFIER_REQUIRED` blockers (`robot/gateway/tangying_robot_gateway/xlerobot_backend.py:135-137`; template comments at `deploy/robot/raspberry-pi/robot-pi.env.example:13-15`). A customer must supply Python modules. There is no default perception stack that makes "pick up the cup" work out of the box.
12. **No remote arming.** Arming requires a physical/interactive local terminal (`robot/gateway/tangying_robot_gateway/run_direct_edge.py:45-48`) and there is no `Arm` RPC (`proto/robot/v1/robot.proto:9-19`). Deliberate and safety-correct, but it means the laptop cannot bring the robot to a task-ready state; a person must be at the robot.
13. **No E-stop recovery from the console.** Clearing a latched stop needs local terminal access, `--operator`, and `--reset-reason` (`docs/sim2real/README.md:177-187`).
14. **No certificate lifecycle management.** Leaves expire in 90 days (`scripts/pair-robot.sh:136`); renewal is a manual re-run of the SSH pairing flow, which the design doc acknowledges (*"renewal repeats the SSH bootstrap deliberately"*, `docs/superpowers/specs/2026-08-18-local-first-runtime-design.md`, §9 item 6). No background renewal, no in-console expiry action.
15. **No hostname provisioning.** Nothing sets a stable, resolvable robot name. The installer's own default (`ROBOT_ADDRESS=xlerobot.local:50051`, `deploy/local/local.env.example:2`) presupposes the user (or the factory image) already configured `xlerobot.local` and a working mDNS responder. If it does not resolve, `pair` dies at `scripts/pair-robot.sh:89`.
16. **No firewall/port documentation or probing.** The robot listens on `0.0.0.0:50051` (`deploy/robot/raspberry-pi/robot-pi.env.example:1`) but no doc or script checks or opens host firewall rules; grep for `ufw|iptables|firewall` in `docs/install/`, `docs/operations/deployment.md`, `docs/sim2real/README.md`, `deploy/` returned nothing relevant.
17. **No error-recovery affordance in the UI for "robot not found".** The console's own connection item tells the user to check cables and press retry (`web/onboarding.js:64-79`) — it has no notion of searching for or adding a robot.

---

## 5. Discovery / pairing / announcement mechanisms in the tree — exact references

**Finding: no discovery, announcement, or auto-pairing mechanism exists anywhere in the tree.**

Everything that exists under the name "pairing":

| Path:line | What it is |
|---|---|
| `scripts/pair-robot.sh:12` | `Usage: robot-agent pair ROBOT_HOST [--ssh-user USER] [--new-ca]` — address is a positional argument |
| `scripts/pair-robot.sh:16` | `ROBOT_HOST=$1` — the address is *taken from the operator*, never discovered |
| `scripts/pair-robot.sh:18` | `SSH_USER=ubuntu` — hardcoded default SSH user |
| `scripts/pair-robot.sh:68-86` | `resolve_robot_ip()` — DNS lookup only; `getent ahostsv4` (74), `dscacheutil` (78), `socket.gethostbyname` (82) |
| `scripts/pair-robot.sh:88-92` | resolved IPv4 validated; **no reachability or port check** |
| `scripts/pair-robot.sh:94-111` | local CA create/reuse; `--new-ca` backups and rotates (`96-103`) |
| `scripts/pair-robot.sh:119-143` | `issue_leaf()`; client leaf at `142`, server leaf at `143` |
| `scripts/pair-robot.sh:154-160` | `deploy_over_ssh()` — `ssh`/`scp` to `$SSH_USER@$ROBOT_HOST`, `sudo install` into `/var/lib/tangying-robot-agent-os/certs`, `sudo systemctl try-restart` |
| `scripts/pair-robot.sh:187-191` | writes `ROBOT_ADDRESS`, `ROBOT_SERVER_NAME`, `ROBOT_CA`, `ROBOT_CERT`, `ROBOT_KEY` into `$CONFIG_DIR/local.env` |
| `internal/robotagent/app.go:75-76`, `260-286` | CLI `pair` subcommand; default `sshUser := "ubuntu"` at `265`; shells out to the script at `281` |
| `internal/robotagent/app.go:98` | help text: `robot-agent pair ROBOT_HOST [--ssh-user USER] [--new-ca]` |
| `tests/install/test_pairing.py:12-34` | the only tests; all set `ROBOT_AGENT_TEST_MODE=1` and `ROBOT_AGENT_PAIR_LOCAL_ROOT` (`19-20`) and **always set both `ROBOT_AGENT_STATE_DIR` and `ROBOT_AGENT_CONFIG_DIR` explicitly** (`22-23`) |
| `docs/superpowers/specs/2026-08-18-local-first-runtime-design.md:243-249` | design-intent section "Pairing and Discovery" describing mDNS `_tangying-robot._tcp.local`; **no implementation** |
| `docs/superpowers/specs/2026-08-18-local-first-runtime-design.md:20` | design intent: "discover or enter a Raspberry Pi address" |

**ROS discovery is explicitly *not* a pairing mechanism and is switched off:** `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST` in both robot units (`deploy/robot/raspberry-pi/tangying-robot-edge.service:14`, `deploy/robot/raspberry-pi/tangying-xlerobot.service:13`), asserted by `tests/deploy/test_deployment_contract.py:22,29`. Even if it were on, it discovers ROS peers for the robot's own internal buses, not the robot to a laptop.

---

## 6. First-run / onboarding / wizard / setup UI in `web/` and `console/`

An onboarding surface **does exist**, and it is well-built — but it starts *after* the connection already exists and has no ability to create one.

- **`web/onboarding.js`** (249 lines) — "Can I use the robot yet?" readiness screen. `ITEM_ORDER = ["connection", "safety", "calibration", "cameras", "map"]` (`web/onboarding.js:20`); `buildReadiness()` sorts and ranks them (`164-191`); `renderReadinessNodes()` renders them (`201-240`).
- Its connection item assumes the wiring already works: when no status has been received it says *"确认机器人已通电、USB 线已插好，然后点'重试连接'"* (`web/onboarding.js:64-79`) and the only action is `navigate("devices")`. **There is no address field, no scan, no pairing action** anywhere in this module.
- The module is careful never to guess: *"It never invents a status: an input it does not have is reported as unknown rather than assumed good"* (`web/onboarding.js:7-9`), `UNKNOWN` state at `15-16,26`.
- Wiring into the app: `web/app.js:1673` (`renderOnboarding`), `web/app.js:1705` (`refreshOnboarding`), `web/app.js:254` (refresh button), `web/app.js:5554` (initial render with `null`).
- **Calibration wizard** (`web/calibration.js:1-25`, `console/server.go:484-497`): the plan and wording are owned by the Python wizard and only rendered by the console. Route defined at `web/console_ui.js:13` ("整机标定"). Server endpoints `GET /v1/calibration/session` and `GET /v1/calibration` (`console/server.go:141-142`).
- **Pages** (`web/console_ui.js:3-14`): `workspace`, `tasks`, `devices`, `diagnostics`, `calibration`, `mapping`. `devices` = "我的机器人" — "查看连接状态和机器人看到的现场" (`web/console_ui.js:5`): status viewing, not configuration.
- **In `console/` (Go):** the string `onboard` appears only in the readiness-rendering context. The only writable configuration surface is the LLM: `PUT /v1/config/llm` (`console/server.go:126`), `UpdateLLM` (`internal/localconfig/settings.go:46-80`), `ConfigStatus` limited to LLM fields (`console/server.go:32-38`).
- **Conclusion for §6:** onboarding exists for *"this connected robot is not ready yet"*; onboarding for *"there is no robot connected yet"* does not exist.

---

## Additional defects found while auditing (verified by execution or direct reading)

### D-1. A path-mismatch hypothesis I formed and then **disproved** (recording it so it is not re-raised)

My initial reading of the constants — `STATE_DIR = $HOME/.local/share/tangying-robot-agent-os` (`scripts/pair-robot.sh:53`) versus `CONFIG_DIR = $HOME/.config/tangying-robot-agent-os` (`scripts/pair-robot.sh:61`) — suggested `pair-robot.sh` might write `local.env` into the wrong directory on Linux relative to where the installer and the systemd unit expect it. **It does not.** I tested this: `pair-robot.sh` writes `local.env` into `CONFIG_DIR` (`scripts/pair-robot.sh:171`), which is exactly where `install_config_example` places it (`scripts/install/local.sh:48` → `scripts/install/common.sh:159-167`) and exactly what the systemd unit reads (`deploy/local/tangying-robot-local-agent.service:8`). Certificates go to `$STATE_DIR/certs` (`scripts/pair-robot.sh:64`), also consistent with the Go resolver (`internal/robotagent/app.go:496-509`) and the install receipt logic (`scripts/install/common.sh:149-157`). **No mismatch on default paths.**

What survives scrutiny is narrower: the two directories are resolved **independently, from separate environment variables**, and the `local.env` written by pairing embeds **absolute** certificate paths under `STATE_DIR` (`scripts/pair-robot.sh:189-191`). Overriding only one of `ROBOT_AGENT_STATE_DIR` / `ROBOT_AGENT_CONFIG_DIR` — which the installer permits (`scripts/install/common.sh:149-167`) and the CLI permits (`internal/robotagent/app.go:483-511`) — therefore produces a pairing that prints `pairing complete` while the Local Agent cannot find its certificates. No test covers this: `tests/install/test_pairing.py:22-23` always sets *both* variables, and `scripts/pair-robot.sh:193` prints success unconditionally with no post-write verification. This is a latent configuration hazard, not a defect on the default path.

### D-2. The Local Agent's default robot address is `127.0.0.1:50051`, not the robot

`cmd/local-agent/main.go:72`:
```go
flags.StringVar(&result.robotAddress, "robot", configValue(values, "ROBOT_ADDRESS", "127.0.0.1:50051"), "Robot Runtime gRPC address")
```
If the config file is missing or unreadable, the agent silently targets loopback. Also note `findConfigPath` returns `""` when `--config` is absent (`cmd/local-agent/main.go:98-111`), so **running `local-agent` by hand with no `--config` reads no config file at all** — the `ROBOT_ADDRESS`/certificate values written by pairing are only picked up when the launcher passes `--config`, which the plist (`deploy/local/com.tangying.robot-agent.plist:9`) and the systemd unit (`deploy/local/tangying-robot-local-agent.service:8`) do, but `make build && ./bin/local-agent` (`docs/operations/fresh-deployment.md:112`) does not.

### D-3. Unverified claim I could not confirm

`docs/operations/fresh-deployment.md` §3 ("路线 B：本地单机（接一台真机）") lists the laptop install, the robot install, and background services, but **never mentions the `pair` step at all**. The pairing commands live in `docs/install/local.md:25-33`, `docs/install/robot-pi-quick.md:29-38`, and `docs/sim2real/README.md:87-94`. The document claiming to be the "单一入口" for cold start (`docs/operations/fresh-deployment.md:3-5`) omits the single most load-bearing step.

### D-4. Robot service port is not reachability-checked anywhere

`pair-robot.sh` resolves DNS and stops (`scripts/pair-robot.sh:88-92`). `scripts/xlerobot_preflight.py` and `scripts/robot-pi-preflight.sh` are documented as non-motion checks that do not connect to the servo bus (`docs/install/robot-pi.md:103`), and I found no TCP probe of `50051` in the tree. A blocked port produces a successful pairing followed by an agent that cannot connect.

---

## (E) Verification status of each claim

| Claim | How verified |
|---|---|
| install roles are exactly `sim`, `local`, `robot-pi`; `cloud` removed | read `install.sh:34-44`, `install.sh:88-92` |
| robot-pi installs the packages/services/files listed in A7 | read `scripts/install/robot-pi.sh` in full (133 lines) and `scripts/install/common.sh` in full (340 lines) |
| install duration | **not measured.** No timing data in the repo. Indirectly large: `lerobot[feetech]==0.4.1` pulls a full ML stack (`pyproject.toml:26-30`), Go toolchain is downloaded (`scripts/install/common.sh:256-279`), and `README.md:48` says only *"首次较久"*. I did not execute any installer. |
| requires root | read `sudo_run` (`scripts/install/common.sh:52-58`) and every call site; confirmed `sudo` use in `scripts/install/robot-pi.sh:5,29,39,59-69,74-81,101,108,123` and `scripts/install/local.sh:20-21,40-42`. Robot role effectively requires root; laptop role escalates for `/usr/local/bin` and apt. |
| requires network | read: curl/git clone/pip/apt/go.dev all reach network (`scripts/install/common.sh:251,267`; `scripts/install/robot-pi.sh:39,63`, `common.sh:305`) |
| pair-robot.sh flow, files created, idempotency | read the script in full; **executed it twice** in `ROBOT_AGENT_TEST_MODE=1` against a temp root and inspected the resulting tree and the generated `local.env` contents |
| pairing reaches robot over SSH with sudo | read `scripts/pair-robot.sh:154-160`; **not executed against a real host** |
| local agent flags/env | read `cmd/local-agent/main.go:41-164`; confirmed the eight `--robot*` flags and the `allowed` config key set at `123-129` |
| no discovery | exhaustive scoped grep (see §5) over all source dirs excluding `vendor/`, `node_modules/`, `XLeRobot/`, `.venv/`; only false positives + one design doc |
| robot starts via systemd, not docker compose | read `deploy/robot/raspberry-pi/tangying-robot-edge-direct.service`; `deploy/robot/navigation/compose.yaml` is a *separate* navigation stack not installed by `install.sh robot-pi` (grep of `scripts/install/` for `compose` returned nothing) |
| gateway listens on 50051 | `robot/gateway/tangying_robot_gateway/run_direct_edge.py:72`; `deploy/robot/raspberry-pi/robot-pi.env.example:1` |
| gateway identity | `robot/gateway/tangying_robot_gateway/xlerobot_backend.py:122` |
| no manual config file edit needed on robot *for the gateway to bind a port* | partly true: `robot-pi.env` is installed from the example and supplies cert/journal paths (`scripts/install/robot-pi.sh:104`), and the gateway's own defaults match (`run_direct_edge.py:85-110`). **But** the serial ports default to `/dev/tangying-left|right` (`deploy/robot/raspberry-pi/robot-pi.env.example:5-6`), which only exist after the hand-edited udev rule (D-1/step 10), and the providers must be added (`…env.example:13-15`). |
| web/console onboarding scope | read `web/onboarding.js` in full, `web/console_ui.js:1-14`, and all `console/server.go` routes |
| arm requires local TTY | read `robot/gateway/tangying_robot_gateway/run_direct_edge.py:44-48,64-71,128-149,172-173`; no `Arm` RPC in `proto/robot/v1/robot.proto:9-19` |
| no `systemctl enable` anywhere | grep for `systemctl`, `enable-linger`, `loginctl`, `bootstrap` across `scripts/ deploy/ docs/ internal/` — only `daemon-reload` and the CLI's `launchctl bootstrap` |
| certificate lifetimes 90d / CA 3650d | `scripts/pair-robot.sh:136` (`-days 90`), `scripts/pair-robot.sh:107` (`-days 3650`) |

**Claims I explicitly could not verify and am not asserting:** actual wall-clock install durations; behaviour against a real Raspberry Pi or real XLeRobot hardware; whether the shipped `xlerobot.local` default ever resolves in a real customer network; whether any out-of-tree/factory image configures the Pi hostname or mDNS. I did not modify any file in the repository.
