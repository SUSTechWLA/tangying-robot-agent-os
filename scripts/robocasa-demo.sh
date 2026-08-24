#!/usr/bin/env bash
# One-command demo: two robots cooperate to move the red block.
#
#   ./scripts/robocasa-demo.sh            full demo (clean scene, run task)
#   ./scripts/robocasa-demo.sh --no-reset reuse a running stack without resetting
#   ./scripts/robocasa-demo.sh --human-speed 0.05   slower robot motion (default 0.02)
#   ./scripts/robocasa-demo.sh --open      open the console in the browser at the end
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

HUMAN_SPEED="${ROBOCASA_HUMAN_SPEED:-0.02}"
RESET=1
OPEN_BROWSER=0

usage() {
    cat <<'EOF'
Usage: scripts/robocasa-demo.sh [--no-reset] [--human-speed SECONDS] [--open]

一键演示：两台机器人配合把红色方块从起始区搬到右侧目标区（经交接区）。
演示前会重置场景，保证每次都能成功展示；任务完成后栈保持运行，
可直接打开 https://127.0.0.1/ 观察用户端（WebGL 数字孪生 + 任务面板）。
EOF
}

die() {
    echo "robocasa-demo: $*" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-reset) RESET=0; shift ;;
        --human-speed) HUMAN_SPEED="$2"; shift 2 ;;
        --open) OPEN_BROWSER=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

docker version >/dev/null 2>&1 || die "Docker is not running (start Docker Desktop first)"
command -v "$CONDA_BIN" >/dev/null 2>&1 || die "conda is not available"
"$CONDA_BIN" env list | grep -q "$ROBOCASA_ENV_NAME" || die "conda env $ROBOCASA_ENV_NAME is missing; run 'make robocasa-install'"

if [[ "$RESET" == "1" ]]; then
    echo "robocasa-demo: resetting scene (stop old stack + fresh cloud)"
    bash "$SCRIPT_DIR/robocasa-fleet.sh" stop >/dev/null 2>&1 || true
    (cd "$CLOUD_DIR" && docker compose down >/dev/null 2>&1 || true)
    sleep 2
fi

echo "robocasa-demo: starting the fleet cloud and the two robot runtimes"
export ROBOCASA_HUMAN_SPEED="$HUMAN_SPEED"
bash "$SCRIPT_DIR/robocasa-fleet.sh" start >/dev/null 2>&1 || {
    echo "robocasa-demo: startup failed; showing logs"
    bash "$SCRIPT_DIR/robocasa-fleet.sh" logs 2>/dev/null | tail -20 || true
    exit 1
}

# 读取凭据（脚本内使用，不打印到日志之外）
FLEET_URL="${FLEET_URL:-https://127.0.0.1:${FLEET_HTTPS_PORT:-443}}"
[[ -f "$CLOUD_DIR/.env" ]] || die "missing $CLOUD_DIR/.env"
# shellcheck disable=SC1090
set -a; source "$CLOUD_DIR/.env"; set +a

python3 - "$FLEET_URL" "$FLEET_OPERATOR_USER" "$FLEET_OPERATOR_PASSWORD" <<'PY'
import json
import ssl
import sys
import time
import urllib.request

base_url, user, password = sys.argv[1], sys.argv[2], sys.argv[3]
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
        if {"robot-1", "robot-2"} <= online:
            return devices
        time.sleep(1)
    raise SystemExit("demo: robots did not come online in time")

print("robocasa-demo: waiting for both robots to come online ...")
token = call("/v1/auth/login", "POST", {"user": user, "password": password})["token"]
wait_ready(token)

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
echo " 任务:         两台机器人配合把红色方块搬到右侧目标区"
echo " 观察:         WebGL 数字孪生 + 任务面板（理解/步骤/动作结果）"
echo " 再次演示:     bash scripts/robocasa-demo.sh   （自动重置场景）"
echo "================================================================"

if [[ "$OPEN_BROWSER" == "1" ]]; then
    open "$FLEET_URL/" 2>/dev/null || true
fi
