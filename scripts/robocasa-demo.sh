#!/usr/bin/env bash
# One-command demo: two robots cooperate to move the red block.
#
#   ./scripts/robocasa-demo.sh            interactive demo (clean scene, wait for user click)
#   ./scripts/robocasa-demo.sh --auto-run full unattended handoff demo
#   ./scripts/robocasa-demo.sh --no-reset reuse a running stack without resetting
#   ./scripts/robocasa-demo.sh --human-speed 0.05   slower robot motion (default 0.04)
#   ./scripts/robocasa-demo.sh --no-open   do not open the console in the browser
#
# The demo always starts from a fresh scene so it can be re-run any number of
# times in front of an audience. After the task succeeds the stack keeps
# running so visitors can inspect the console (3D WebGL world, mission rail,
# camera evidence) on their own.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
CLOUD_DIR="$ROOT_DIR/deploy/cloud"
CONDA_BIN="${CONDA_BIN:-conda}"
ROBOCASA_ENV_NAME="${ROBOCASA_ENV_NAME:-tangying-robocasa}"

HUMAN_SPEED="${ROBOCASA_HUMAN_SPEED:-0.04}"
RESET=1
OPEN_BROWSER=1
DEMO_MODE="interactive"

usage() {
    cat <<'EOF'
Usage: scripts/robocasa-demo.sh [--auto-run] [--no-reset] [--human-speed SECONDS] [--no-open]

一键演示：两台机器人配合把红色方块从起始区搬到右侧目标区（经交接区）。
默认会重置场景并打开控制台，等待用户输入自然语言、点击“创建并开始任务”；
使用 --auto-run 才会自动创建并执行任务。任务完成后栈保持运行。
EOF
}

die() {
    echo "robocasa-demo: $*" >&2
    exit 1
}

stop_cross_worktree_profile() {
    local common_dir repository_root pid_file pid command_line
    common_dir="$(git -C "$ROOT_DIR" rev-parse --git-common-dir 2>/dev/null || true)"
    [[ -n "$common_dir" ]] || return 0
    repository_root="${common_dir%/.git}"
    shopt -s nullglob
    local pid_files=(
        "$repository_root"/artifacts/robocasa-harness/run/*.pid
        "$repository_root"/.worktrees/*/artifacts/robocasa-harness/run/*.pid
    )
    for pid_file in "${pid_files[@]}"; do
        pid="$(tr -d '[:space:]' < "$pid_file" 2>/dev/null || true)"
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
        case "$command_line" in
            *tangying_robocasa.fleet_server*|*/edge-worker*)
                echo "robocasa-demo: stopping stale profile process $(basename "$pid_file" .pid) (pid $pid)"
                kill "$pid" 2>/dev/null || true
                rm -f "$pid_file"
                ;;
        esac
    done
    local port attempts
    for port in "${ROBOCASA_PORT_1:-51051}" "${ROBOCASA_PORT_2:-51052}"; do
        attempts=0
        while nc -z 127.0.0.1 "$port" >/dev/null 2>&1 && [[ "$attempts" -lt 30 ]]; do
            attempts=$((attempts + 1))
            sleep 0.1
        done
        if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
            die "stale RoboCasa process did not release port $port"
        fi
    done
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --auto-run) DEMO_MODE="auto"; shift ;;
            --no-reset) RESET=0; shift ;;
            --human-speed)
                [[ $# -ge 2 ]] || die "--human-speed requires a value"
                HUMAN_SPEED="$2"
                shift 2
                ;;
            --open) OPEN_BROWSER=1; shift ;;
            --no-open) OPEN_BROWSER=0; shift ;;
            --help|-h) usage; return 2 ;;
            *) die "unknown option: $1" ;;
        esac
    done
}

main() {
    parse_args "$@" || return 0

    docker version >/dev/null 2>&1 || die "Docker is not running (start Docker Desktop first)"
    command -v "$CONDA_BIN" >/dev/null 2>&1 || die "conda is not available"
    "$CONDA_BIN" env list | grep -q "$ROBOCASA_ENV_NAME" || die "conda env $ROBOCASA_ENV_NAME is missing; run 'make robocasa-install'"

    if [[ "$RESET" == "1" ]]; then
        echo "robocasa-demo: resetting scene (stop old stack + fresh cloud)"
        bash "$SCRIPT_DIR/robocasa-fleet.sh" stop >/dev/null 2>&1 || true
        stop_cross_worktree_profile
        # This is the explicit clean-demo path: discard local control-plane
        # task/database state as well as containers so credentials from a
        # different worktree cannot leave MySQL unusable.
        (cd "$CLOUD_DIR" && docker compose down -v --remove-orphans >/dev/null 2>&1 || true)
        sleep 2
    fi

    echo "robocasa-demo: starting the fleet cloud and the two robot runtimes"
    export ROBOCASA_HUMAN_SPEED="$HUMAN_SPEED"
    bash "$SCRIPT_DIR/robocasa-fleet.sh" start || {
        echo "robocasa-demo: startup failed; showing logs"
        tail -n 40 "$ROOT_DIR/logs"/robocasa-*.log 2>/dev/null || true
        exit 1
    }

# 读取凭据（脚本内使用，不打印到日志之外）
    FLEET_URL="${FLEET_URL:-https://127.0.0.1:${FLEET_HTTPS_PORT:-443}}"
    [[ -f "$CLOUD_DIR/.env" ]] || die "missing $CLOUD_DIR/.env"
    # shellcheck disable=SC1090
    set -a; source "$CLOUD_DIR/.env"; set +a

    python3 - "$FLEET_URL" "$FLEET_OPERATOR_USER" "$FLEET_OPERATOR_PASSWORD" "$DEMO_MODE" <<'PY'
import json
import ssl
import sys
import time
import urllib.request

base_url, user, password, mode = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def call(path, method="GET", body=None, token=""):
    request = urllib.request.Request(base_url + path, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
        request.data = json.dumps(body).encode()
    if token:
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, context=ctx, timeout=15) as response:
        raw = response.read()
    return json.loads(raw) if raw else None

def wait_ready(token, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        devices = call("/v1/devices", token=token)
        online = {d["robotId"] for d in devices if d.get("online")}
        world = call("/v1/world", token=token)
        entities = world.get("entities", {})
        robots = world.get("robots", {})
        # Device heartbeats arrive before the first Runtime observation. Wait
        # for the first MuJoCo scene/robot sample as well, which also warms the
        # background camera cache before a user can launch physical motion.
        if (
            {"robot-1", "robot-2"} <= online
            and "red-block" in entities
            and {"robot-1", "robot-2"} <= set(robots)
        ):
            return devices
        time.sleep(1)
    raise SystemExit("demo: robots or initial MuJoCo world did not become ready in time")

print("robocasa-demo: waiting for both robots to come online ...")
token = call("/v1/auth/login", "POST", {"user": user, "password": password})["token"]
wait_ready(token)

if mode != "auto":
    print("robocasa-demo: fresh scene is ready; create the task from the console")
    raise SystemExit(0)

request = "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区"
task = call("/v1/tasks", "POST", {"request": request, "adapter": "robocasa"}, token)
task_id = task["id"]
print(f"robocasa-demo: task {task_id} created: {request}")
call(f"/v1/tasks/{task_id}/approve", "POST", None, token)

started = time.time()
state = "READY"
while time.time() - started < 150:
    state = call(f"/v1/tasks/{task_id}", token=token)["state"]
    if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
        break
    time.sleep(1)
if state != "SUCCEEDED":
    view = call(f"/v1/tasks/{task_id}/intents", token=token)
    reasons = [i.get("error", "") for i in view.get("intents", []) if i.get("error")]
    print("demo failed; intents:", json.dumps(view.get("intents"), ensure_ascii=False)[:400])
    if any("OBJECT_NOT_FOUND" in r for r in reasons):
        print("提示: 方块已被搬走（场景不是初始状态）。默认模式会自动重置场景；")
        print("      请直接运行: bash scripts/robocasa-demo.sh")
    raise SystemExit(f"demo: task ended {state}")

view = call(f"/v1/tasks/{task_id}/intents", token=token)
verdicts = [i.get("harnessStatus") for i in view.get("intents", []) if i.get("harnessStatus")]
print(f"robocasa-demo: task SUCCEEDED in {time.time() - started:.0f}s | Harness: {', '.join(verdicts) or 'n/a'}")
PY

    echo ""
    echo "=========================== 演示就绪 ==========================="
    echo " 用户端控制台:  $FLEET_URL/"
    echo " 登录账号:     $FLEET_OPERATOR_USER"
    echo " 登录密码:     $FLEET_OPERATOR_PASSWORD"
    if [[ "$DEMO_MODE" == "auto" ]]; then
        echo " 状态:         自动演示已完成，可查看任务与环境证据"
    else
        echo " 下一步:       输入任务，点击“创建并开始任务”"
    fi
    echo " 任务:         两台机器人经交接区搬运红色方块"
    echo " 观察:         WebGL 数字孪生 + 场景内接力编排 + 动作结果"
    echo " 再次演示:     bash scripts/robocasa-demo.sh   （自动重置场景）"
    echo "================================================================"

    if [[ "$OPEN_BROWSER" == "1" ]]; then
        open "$FLEET_URL/" 2>/dev/null || true
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
