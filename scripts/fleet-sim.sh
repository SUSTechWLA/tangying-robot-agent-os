#!/usr/bin/env bash
# Fleet simulation: two MuJoCo robot runtimes in one process share one logical
# handoff block. Each runtime has its own edge worker and both point at cloud.
#
#   ./scripts/fleet-sim.sh start   start both sims and both edge workers
#   ./scripts/fleet-sim.sh handoff run the shared-block handoff end to end
#   ./scripts/fleet-sim.sh stop    stop everything
#   ./scripts/fleet-sim.sh status
#   ./scripts/fleet-sim.sh logs    follow the two edge worker logs
#
# Ports: sims on 50051/50052 (plaintext gRPC, simulation only); edge workers
# dial the cloud over HTTPS 443 (data plane) and mTLS gRPC 8444 (presence).
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
ARTIFACTS_DIR="${FLEET_SIM_ARTIFACTS_DIR:-$ROOT_DIR/artifacts/fleet-sim}"
RUN_DIR="$ARTIFACTS_DIR/run"
LOG_DIR="${FLEET_SIM_LOG_DIR:-$ROOT_DIR/logs}"
CLOUD_DIR="$ROOT_DIR/deploy/cloud"
CERT_DIR="$CLOUD_DIR/certs"
PYTHON="${FLEET_SIM_PYTHON:-$ROOT_DIR/.venv/bin/python}"
EDGE_WORKER="${FLEET_SIM_EDGE_WORKER:-$ROOT_DIR/bin/edge-worker}"
FLEET_URL="${FLEET_URL:-https://127.0.0.1:${FLEET_HTTPS_PORT:-443}}"
FLEET_GRPC="${FLEET_GRPC:-127.0.0.1:${FLEET_GRPC_PORT:-8444}}"
SIM_PORT_1="${FLEET_SIM_PORT_1:-50051}"
SIM_PORT_2="${FLEET_SIM_PORT_2:-50052}"

die() {
    echo "fleet-sim: $*" >&2
    exit 1
}

load_env() {
    [[ -f "$CLOUD_DIR/.env" ]] || die "no $CLOUD_DIR/.env; run 'scripts/fleet-up.sh up' first"
    # shellcheck disable=SC1090
    set -a
    # shellcheck disable=SC1090
    source "$CLOUD_DIR/.env"
    set +a
}

require_certs() {
    for file in fleet-ca.crt robot-1.crt robot-1.key robot-2.crt robot-2.key; do
        [[ -f "$CERT_DIR/$file" ]] || die "missing $CERT_DIR/$file; run 'scripts/fleet-up.sh up' first"
    done
}

device_token_for() {
    local robot_id="$1" entry
    IFS=',' read -ra credentials <<< "${FLEET_DEVICE_CREDENTIALS:-}"
    for entry in "${credentials[@]}"; do
        if [[ "${entry%%:*}" == "$robot_id" && -n "${entry#*:}" ]]; then
            echo "${entry#*:}"
            return 0
        fi
    done
    die "no device credential provisioned for $robot_id"
}

record_pid() {
    local name="$1" pid="$2"
    mkdir -p "$RUN_DIR"
    echo "$pid" > "$RUN_DIR/$name.pid"
}

is_running() {
    local name="$1"
    [[ -f "$RUN_DIR/$name.pid" ]] || return 1
    local pid
    pid="$(cat "$RUN_DIR/$name.pid")"
    kill -0 "$pid" 2>/dev/null
}

launch() {
    local name="$1" logfile="$2"
    shift 2
    if is_running "$name"; then
        echo "fleet-sim: $name already running (pid $(cat "$RUN_DIR/$name.pid"))"
        return 0
    fi
    echo "fleet-sim: launching $name -> $logfile"
    mkdir -p "$LOG_DIR"
    # `env` execs the command, so the recorded PID is the process itself.
    nohup env "$@" >> "$logfile" 2>&1 &
    record_pid "$name" $!
    sleep 1
    is_running "$name" || die "$name failed to start; see $logfile"
}

start() {
    load_env
    require_certs
    robot_1_token="$(device_token_for robot-1)"
    robot_2_token="$(device_token_for robot-2)"
    [[ -x "$EDGE_WORKER" ]] || { echo "fleet-sim: building edge-worker"; (cd "$ROOT_DIR" && go build -o "$EDGE_WORKER" ./cmd/edge-worker); }
    mkdir -p "$ARTIFACTS_DIR"

    launch "sim-fleet" "$LOG_DIR/fleet-sim-runtime.log" \
        "$PYTHON" -m tangying_sim.fleet_server \
        --sender-listen "127.0.0.1:$SIM_PORT_1" \
        --receiver-listen "127.0.0.1:$SIM_PORT_2" \
        --seed 7 --human-speed ${FLEET_HUMAN_SPEED:-0.02}

    launch "edge-robot-1" "$LOG_DIR/fleet-sim-edge-robot-1.log" \
        EDGE_ROBOT_ID=robot-1 \
        EDGE_FLEET_URL="$FLEET_URL" \
        EDGE_DEVICE_TOKEN="$robot_1_token" \
        EDGE_RUNTIME_ADDR="127.0.0.1:$SIM_PORT_1" \
        EDGE_RUNTIME_INSECURE=1 \
        EDGE_FLEET_GRPC="$FLEET_GRPC" \
        EDGE_MTLS_CA="$CERT_DIR/fleet-ca.crt" \
        EDGE_MTLS_CERT="$CERT_DIR/robot-1.crt" \
        EDGE_MTLS_KEY="$CERT_DIR/robot-1.key" \
        EDGE_MTLS_SERVER_NAME=localhost \
        EDGE_FLEET_CA="$CERT_DIR/fleet-ca.crt" \
        EDGE_TASK_SOURCE=http \
        EDGE_TELEMETRY_INTERVAL=500ms \
        "$EDGE_WORKER"
    launch "edge-robot-2" "$LOG_DIR/fleet-sim-edge-robot-2.log" \
        EDGE_ROBOT_ID=robot-2 \
        EDGE_FLEET_URL="$FLEET_URL" \
        EDGE_DEVICE_TOKEN="$robot_2_token" \
        EDGE_RUNTIME_ADDR="127.0.0.1:$SIM_PORT_2" \
        EDGE_RUNTIME_INSECURE=1 \
        EDGE_FLEET_GRPC="$FLEET_GRPC" \
        EDGE_MTLS_CA="$CERT_DIR/fleet-ca.crt" \
        EDGE_MTLS_CERT="$CERT_DIR/robot-2.crt" \
        EDGE_MTLS_KEY="$CERT_DIR/robot-2.key" \
        EDGE_MTLS_SERVER_NAME=localhost \
        EDGE_FLEET_CA="$CERT_DIR/fleet-ca.crt" \
        EDGE_TASK_SOURCE=http \
        EDGE_TELEMETRY_INTERVAL=500ms \
        "$EDGE_WORKER"

    echo "fleet-sim: two robots are online; run 'scripts/fleet-sim.sh demo'"
}

stop() {
    for name in edge-robot-2 edge-robot-1 sim-fleet; do
        if is_running "$name"; then
            local pid
            pid="$(cat "$RUN_DIR/$name.pid")"
            echo "fleet-sim: stopping $name (pid $pid)"
            kill "$pid" 2>/dev/null || true
            for _ in $(seq 1 20); do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.25
            done
            kill -9 "$pid" 2>/dev/null || true
            rm -f "$RUN_DIR/$name.pid"
        fi
    done
    echo "fleet-sim: stopped"
}

status() {
    for name in sim-fleet edge-robot-1 edge-robot-2; do
        if is_running "$name"; then
            echo "$name: running (pid $(cat "$RUN_DIR/$name.pid"))"
        else
            echo "$name: stopped"
        fi
    done
}

logs() {
    local target="${1:-}"
    case "$target" in
        edge-1) tail -f "$LOG_DIR/fleet-sim-edge-robot-1.log" ;;
        edge-2) tail -f "$LOG_DIR/fleet-sim-edge-robot-2.log" ;;
        sim-1|sim-2|sim) tail -f "$LOG_DIR/fleet-sim-runtime.log" ;;
        *) tail -f "$LOG_DIR"/fleet-sim-*.log ;;
    esac
}

# demo creates the multi-robot task through the cloud API and waits for the
# full closed loop: robot-1 picks+places, then robot-2 picks+places.
demo() {
    load_env
    local base="$FLEET_URL"
    local token
    token="$(curl -fsS -k -X POST "$base/v1/auth/login" \
        -H 'Content-Type: application/json' \
        --data "{\"user\":\"$FLEET_OPERATOR_USER\",\"password\":\"$FLEET_OPERATOR_PASSWORD\"}" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
    local task_id
    task_id="$(curl -fsS -k -X POST "$base/v1/tasks" \
        -H 'Content-Type: application/json' \
        -H "Authorization: Bearer $token" \
        --data '{"request":"让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区","adapter":"mujoco"}' \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
    echo "fleet-sim: created task $task_id, approving"
    curl -fsS -k -X POST "$base/v1/tasks/$task_id/approve" \
        -H "Authorization: Bearer $token" >/dev/null
    local state=""
    for _ in $(seq 1 120); do
        state="$(curl -fsS -k "$base/v1/tasks/$task_id" \
            -H "Authorization: Bearer $token" \
            | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')"
        case "$state" in
            SUCCEEDED|FAILED|CANCELLED) break ;;
        esac
        sleep 2
    done
    echo "fleet-sim: task $task_id final state: $state"
    if [[ "$state" != "SUCCEEDED" ]]; then
        curl -fsS -k "$base/v1/tasks/$task_id/intents" -H "Authorization: Bearer $token" | python3 -m json.tool
        exit 1
    fi
    echo "fleet-sim: multi-robot closed loop SUCCEEDED"
}

OPERATION="${1:-start}"
case "$OPERATION" in
    start) start ;;
    stop) stop ;;
    status) status ;;
    logs) logs "${2:-}" ;;
    demo|handoff) demo ;;
    --help|-h)
        echo "Usage: scripts/fleet-sim.sh {start|stop|status|logs [edge-1|edge-2|sim-1|sim-2]|handoff}" ;;
    *) echo "unknown operation: $OPERATION" >&2; exit 2 ;;
esac
