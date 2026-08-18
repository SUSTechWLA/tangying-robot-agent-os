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
"$ROOT/.venv/bin/python" -m tangying_sim.server --listen "127.0.0.1:$robot_port" --seed "$SEED" >"$temporary/robot.log" 2>&1 &
robot_pid=$!
go run ./cmd/local-agent --dev-insecure \
  --listen "127.0.0.1:$local_port" \
  --robot "127.0.0.1:$robot_port" \
  --data-dir "$temporary/local-agent" >"$temporary/local-agent.log" 2>&1 &
local_pid=$!

attempt=0
until curl -fsS "$local_url/healthz" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 100 ]; then
    echo "error: Local Agent did not become healthy" >&2
    sed -n '1,160p' "$temporary/local-agent.log" >&2
    exit 1
  fi
  sleep 0.1
done

task_json=$(curl -fsS -X POST "$local_url/v1/tasks" \
  -H 'Content-Type: application/json' \
  --data '{"request":"把红色杯子放进右侧收纳盒","adapter":"mujoco"}')
task_id=$(printf '%s' "$task_json" | "$ROOT/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -fsS -X POST "$local_url/v1/tasks/$task_id/approve" >/dev/null

state=""
finished=""
attempt=0
while [ "$state" != "SUCCEEDED" ] && [ "$state" != "FAILED" ] && [ "$state" != "CANCELLED" ]; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 300 ]; then
    echo "error: demo task did not finish" >&2
    sed -n '1,160p' "$temporary/local-agent.log" >&2
    exit 1
  fi
  finished=$(curl -fsS "$local_url/v1/tasks/$task_id")
  state=$(printf '%s' "$finished" | "$ROOT/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["state"])')
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
