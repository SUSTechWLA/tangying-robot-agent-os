#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
CHECK_ONLY=0
SEED=7

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=1 ;;
    --seed)
      shift
      [ "$#" -gt 0 ] || { echo "error: --seed requires a value" >&2; exit 2; }
      SEED=$1
      ;;
    -h|--help)
      echo "Usage: robot-agent demo [--seed N]"
      exit 0
      ;;
    *) echo "error: unknown demo option: $1" >&2; exit 2 ;;
  esac
  shift
done

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "error: missing prerequisite: $1" >&2; exit 1; }
}

require go
require curl
[ -x "$ROOT/.venv/bin/python" ] || { echo "error: run ./install.sh sim first (.venv is missing)" >&2; exit 1; }
"$ROOT/.venv/bin/python" -c 'import grpc, mujoco, tangying_sim' >/dev/null

if [ "$CHECK_ONLY" = "1" ]; then
  echo "demo prerequisites: OK"
  exit 0
fi

temporary=$(mktemp -d "${TMPDIR:-/tmp}/tangying-robot-demo.XXXXXX")
local_pid=""
robot_pid=""

cleanup() {
  for pid in "$local_pid" "$robot_pid"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  rm -rf "$temporary"
}
trap cleanup EXIT INT TERM

free_port() {
  "$ROOT/.venv/bin/python" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

local_port=$(free_port)
robot_port=$(free_port)
local_url="http://127.0.0.1:$local_port"

cd "$ROOT"
# Run the actual binary so cleanup owns the server PID, not a go-run parent
# whose compiled child can survive after the parent is terminated.
go build -o "$temporary/local-agent-bin" ./cmd/local-agent
# Camera perception is the commissioned path. The deterministic ground-truth
# debug runtime does not identify the observations it returns, so a physical
# write on it cannot be confirmed from fresh evidence and fails closed.
"$ROOT/.venv/bin/python" -m tangying_sim.server --listen "127.0.0.1:$robot_port" --seed "$SEED" \
  --perception rgbd --scene tabletop >"$temporary/robot.log" 2>&1 &
robot_pid=$!
"$temporary/local-agent-bin" --dev-insecure \
  --robot-safety-profile simulation \
  --listen "127.0.0.1:$local_port" \
  --robot "127.0.0.1:$robot_port" \
  --data-dir "$temporary/local-agent" >"$temporary/local-agent.log" 2>&1 &
local_pid=$!

# HTTP liveness alone does not mean the Runtime has finished loading MuJoCo.
# Both APIs below are backed by this Local Agent's real gRPC Info / Observe
# calls, so readiness also covers its own connection retry/backoff state.
if ! "$ROOT/.venv/bin/python" - "$local_url" "$local_pid" "$robot_pid" <<'PY'
import json
import math
import os
import sys
import time
from datetime import datetime
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

url, *pids = sys.argv[1:]
opener = build_opener(ProxyHandler({}))
started = time.time()
deadline = time.monotonic() + 30

def get(path):
    with opener.open(url + path, timeout=1) as response:
        return json.load(response)

while time.monotonic() < deadline:
    for pid in pids:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            raise SystemExit("error: demo service exited before Runtime readiness") from None
    try:
        runtime = get("/v1/runtime")
        if (runtime["RobotID"], runtime["Adapter"]) != ("xlerobot-mujoco-tabletop", "mujoco"):
            raise SystemExit("error: demo Runtime identity does not match the simulation")
        if not runtime["ProtocolVersion"].startswith("1.") or not runtime["CatalogRevision"]:
            raise SystemExit("error: demo Runtime returned an incompatible tool catalog")
        if not runtime["Ready"]:
            raise SystemExit("error: demo Runtime is not ready for manipulation")
        telemetry = get("/v1/telemetry?adapter=mujoco")
        if telemetry["hasLatest"]:
            observation = telemetry["latest"]
            if (observation["robotId"], observation["adapter"]) != (runtime["RobotID"], runtime["Adapter"]):
                raise SystemExit("error: demo observation identity does not match its Runtime")
            observed_at = datetime.fromisoformat(observation["observedAt"].replace("Z", "+00:00")).timestamp()
            entities = {entity["entityId"]: entity for entity in observation.get("entities", [])}
            poses = [entities.get(name, {}).get("pose", []) for name in ("red-cup", "right-bin")]
            valid_poses = all(len(pose) == 7 and all(math.isfinite(value) for value in pose) for pose in poses)
            if (observation["mode"] == "SIMULATION" and not observation["emergencyStopped"]
                    and started - 1 <= observed_at and -1 <= time.time() - observed_at <= 5
                    and valid_poses):
                break
    except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
        pass
    time.sleep(0.1)
else:
    raise SystemExit("error: demo Runtime did not provide ready information and a fresh scene within 30 seconds")
PY
then
  sed -n '1,160p' "$temporary/local-agent.log" >&2
  exit 1
fi

curl_local() {
  curl --noproxy '127.0.0.1' --connect-timeout 1 --max-time 5 "$@"
}

task_json=$(curl_local -fsS -X POST "$local_url/v1/tasks" \
  -H 'Content-Type: application/json' \
  --data '{"request":"把红色杯子放进右侧收纳盒","adapter":"mujoco"}')
task_id=$(printf '%s' "$task_json" | "$ROOT/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl_local -fsS -X POST "$local_url/v1/tasks/$task_id/approve" >/dev/null

state=""
finished=""
attempt=0
while :; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 300 ]; then
    echo "error: demo task did not finish" >&2
    sed -n '1,160p' "$temporary/local-agent.log" >&2
    exit 1
  fi
  finished=$(curl_local -fsS "$local_url/v1/tasks/$task_id")
  state=$(printf '%s' "$finished" | "$ROOT/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["state"])')
  case "$state" in
    SUCCEEDED|FAILED|CANCELLED|RECOVERABLE_FAILURE|FAILED_SAFE|SAFETY_STOPPED|BLOCKED|WAITING_USER) break ;;
  esac
  sleep 0.1
done
if [ "$state" != "SUCCEEDED" ]; then
  echo "error: demo task ended in $state" >&2
  printf '%s\n' "$finished" >&2
  sed -n '1,160p' "$temporary/local-agent.log" >&2
  exit 1
fi

event_count=$(printf '%s' "$finished" | "$ROOT/.venv/bin/python" -c 'import json,sys; print(len(json.load(sys.stdin)["events"]))')
echo "demo succeeded: task=$task_id state=$state events=$event_count seed=$SEED"
