#!/usr/bin/env bash
set -euo pipefail

# Lifecycle wrapper for the optional Gazebo Harmonic home backend. It keeps
# the navigation token and RTAB-Map database private and uses a separate
# Compose project/ROS domain so the two simulators cannot share topics.
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACTS_DIR="${GAZEBO_HOUSE_ARTIFACTS_DIR:-$ROOT_DIR/artifacts/gazebo-house}"
CONFIG="$ARTIFACTS_DIR/gazebo-house.env"
COMPOSE=(docker compose -p tangying-gazebo-house -f "$ROOT_DIR/deploy/robot/navigation/gazebo-house.compose.yaml")
OPERATION="${1:-}"
shift || true
BUILD=0
MODE="${TANGYING_NAVIGATION_MODE:-mapping}"
PORT="${TANGYING_NAVIGATION_PORT:-18791}"

usage() { echo "Usage: scripts/gazebo-house-stack.sh {start|stop|restart|status|logs} [--build] [--mode mapping|localization] [--port PORT]"; }
[[ "$OPERATION" =~ ^(start|stop|restart|status|logs)$ ]] || { usage >&2; exit 2; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build) BUILD=1; shift ;;
    --mode) MODE="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ "$MODE" == mapping || "$MODE" == localization ]] || { echo "mode must be mapping or localization" >&2; exit 2; }
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || { echo "port must be 1-65535" >&2; exit 2; }
mkdir -p "$ARTIFACTS_DIR"
if [[ ! -f "$CONFIG" ]]; then
  token="$($ROOT_DIR/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  (umask 077; printf 'TANGYING_NAVIGATION_TOKEN=%s\n' "$token" > "$CONFIG")
fi
set -a
# This generated file contains only the private token.
source "$CONFIG"
set +a
export TANGYING_NAVIGATION_MODE="$MODE" TANGYING_NAVIGATION_PORT="$PORT"

case "$OPERATION" in
  start|restart)
    args=(up -d)
    if (( BUILD )); then
      args+=(--build)
    fi
    [[ "$OPERATION" == restart ]] && args+=(--force-recreate)
    "${COMPOSE[@]}" "${args[@]}"
    echo "Gazebo 家庭仿真已启动：http://127.0.0.1:${PORT}（模式：${MODE}）"
    ;;
  stop) "${COMPOSE[@]}" down ;;
  status)
    "${COMPOSE[@]}" ps
    curl -fsS --max-time 2 -H "Authorization: Bearer $TANGYING_NAVIGATION_TOKEN" "http://127.0.0.1:$PORT/v1/navigation/map" \
      | "$ROOT_DIR/.venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({k:d.get(k) for k in ("ready","mode","scene","localizationState","mapRevision","readinessBlockers")},ensure_ascii=False))'
    ;;
  logs) "${COMPOSE[@]}" logs --tail 100 ;;
esac
