#!/usr/bin/env bash
# One RoboCasa kitchen, two XLeRobot Runtime endpoints, two Edge Workers, and
# the Fleet cloud. This profile keeps one authoritative MjData and therefore
# never fakes handoff by copying object state between simulators.
#
#   ./scripts/robocasa-fleet.sh start
#   ./scripts/robocasa-fleet.sh handoff
#   ./scripts/robocasa-fleet.sh status
#   ./scripts/robocasa-fleet.sh logs
#   ./scripts/robocasa-fleet.sh stop
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
CLOUD_DIR="$ROOT_DIR/deploy/cloud"
CERT_DIR="$CLOUD_DIR/certs"
ARTIFACTS_DIR="${ROBOCASA_FLEET_ARTIFACTS_DIR:-$ROOT_DIR/artifacts/robocasa-harness}"
RUN_DIR="$ARTIFACTS_DIR/run"
LOG_DIR="${ROBOCASA_FLEET_LOG_DIR:-$ROOT_DIR/logs}"
EDGE_WORKER="${ROBOCASA_EDGE_WORKER:-$ROOT_DIR/bin/edge-worker}"
CONDA_BIN="${CONDA_BIN:-conda}"
ROBOCASA_ENV_NAME="${ROBOCASA_ENV_NAME:-tangying-robocasa}"
SIM_PORT_1="${ROBOCASA_PORT_1:-51051}"
SIM_PORT_2="${ROBOCASA_PORT_2:-51052}"
FLEET_URL="${FLEET_URL:-https://127.0.0.1:${FLEET_HTTPS_PORT:-443}}"
FLEET_GRPC="${FLEET_GRPC:-127.0.0.1:${FLEET_GRPC_PORT:-8444}}"

die() {
    echo "robocasa-fleet: $*" >&2
    exit 1
}

cloud_healthy() {
    curl -fsS -k "$FLEET_URL/healthz" >/dev/null 2>&1
}

cloud_matches_profile() {
    local container
    [[ -f "$CLOUD_DIR/.env" ]] || return 1
    container="$(cd "$CLOUD_DIR" && docker compose --env-file .env ps -q fleet-control-plane 2>/dev/null)"
    [[ -n "$container" ]] || return 1
    local environment
    environment="$(docker inspect "$container" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null)"
    # A Docker Compose project can outlive the worktree that created it. Match
    # values without printing them so a healthy container with stale
    # credentials is rebuilt instead of making both Edge Workers fail auth.
    # shellcheck disable=SC1090
    set -a; source "$CLOUD_DIR/.env"; set +a
    local actual_operator_user="" actual_operator_password=""
    local actual_device_credentials="" actual_world_id="" actual_auth_mode=""
    local key value
    while IFS='=' read -r key value; do
        case "$key" in
            FLEET_OPERATOR_USER) actual_operator_user="$value" ;;
            FLEET_OPERATOR_PASSWORD) actual_operator_password="$value" ;;
            FLEET_DEVICE_CREDENTIALS) actual_device_credentials="$value" ;;
            FLEET_WORLD_ID) actual_world_id="$value" ;;
            FLEET_AUTH_MODE) actual_auth_mode="$value" ;;
        esac
    done <<< "$environment"
    [[ "$actual_operator_user" == "$FLEET_OPERATOR_USER" ]] &&
        [[ "$actual_operator_password" == "$FLEET_OPERATOR_PASSWORD" ]] &&
        [[ "$actual_device_credentials" == "$FLEET_DEVICE_CREDENTIALS" ]] &&
        [[ "$actual_world_id" == "robocasa-handoff-v1" ]] &&
        [[ "$actual_auth_mode" == "demo" ]]
}

load_env() {
    [[ -f "$CLOUD_DIR/.env" ]] || die "missing $CLOUD_DIR/.env"
    set -a
    # shellcheck disable=SC1090
    source "$CLOUD_DIR/.env"
    set +a
    FLEET_URL="${FLEET_URL:-https://127.0.0.1:${FLEET_HTTPS_PORT:-443}}"
    FLEET_GRPC="${FLEET_GRPC:-127.0.0.1:${FLEET_GRPC_PORT:-8444}}"
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
    mkdir -p "$RUN_DIR"
    echo "$2" > "$RUN_DIR/$1.pid"
}

is_running() {
    local name="$1" pid
    [[ -f "$RUN_DIR/$name.pid" ]] || return 1
    pid="$(<"$RUN_DIR/$name.pid")"
    kill -0 "$pid" 2>/dev/null
}

launch() {
    local name="$1" logfile="$2"
    shift 2
    if is_running "$name"; then
        echo "robocasa-fleet: $name already running (pid $(<"$RUN_DIR/$name.pid"))"
        return
    fi
    mkdir -p "$LOG_DIR" "$RUN_DIR"
    echo "robocasa-fleet: launching $name -> $logfile"
    nohup env "$@" >>"$logfile" 2>&1 &
    record_pid "$name" "$!"
    sleep 1
    is_running "$name" || die "$name failed; see $logfile"
}

wait_for_port() {
    local port="$1" name="$2" attempts=0
    while [[ $attempts -lt 120 ]]; do
        if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
            return 0
        fi
        is_running robocasa-runtime || die "$name stopped during initialization; see $LOG_DIR/robocasa-runtime.log"
        attempts=$((attempts + 1))
        sleep 1
    done
    die "$name did not listen on 127.0.0.1:$port within 120s"
}

assert_sim_ports_available() {
    # Re-running this profile is idempotent when its recorded Runtime is alive.
    if is_running robocasa-runtime; then
        return 0
    fi
    local port
    for port in "$SIM_PORT_1" "$SIM_PORT_2"; do
        if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
            die "port 127.0.0.1:$port belongs to another worktree or process; use robocasa-demo.sh for an explicit fresh reset"
        fi
    done
}

ensure_cloud() {
    local cloud_was_healthy=0
    if cloud_healthy; then
        cloud_was_healthy=1
    fi
    if [[ "${ROBOCASA_REBUILD_CLOUD:-0}" != "1" ]] && cloud_healthy &&
        grep -qE '^FLEET_DEVICE_CREDENTIALS=.+:.+' "$CLOUD_DIR/.env" && cloud_matches_profile; then
        return
    fi
    echo "robocasa-fleet: starting or upgrading Fleet cloud"
    FLEET_AUTH_MODE=demo FLEET_WORLD_ID=robocasa-handoff-v1 bash "$SCRIPT_DIR/fleet-up.sh" up --build
    if [[ "$cloud_was_healthy" == "0" ]]; then
        mkdir -p "$RUN_DIR"
        : > "$RUN_DIR/started-cloud"
    fi
}

start_edge() {
    local robot_id="$1" port="$2" token="$3"
    launch "edge-$robot_id" "$LOG_DIR/robocasa-edge-$robot_id.log" \
        EDGE_ROBOT_ID="$robot_id" \
        EDGE_ADAPTER=robocasa \
        EDGE_POLICY_MODE=deterministic \
        EDGE_ROBOT_MODEL=xlerobot-sim \
        EDGE_WORLD_ID=robocasa-handoff-v1 \
        EDGE_TRANSFORM_REVISION=robocasa-world-v1 \
        EDGE_FLEET_URL="$FLEET_URL" \
        EDGE_DEVICE_TOKEN="$token" \
        EDGE_RUNTIME_ADDR="127.0.0.1:$port" \
        EDGE_RUNTIME_INSECURE=1 \
        EDGE_FLEET_GRPC="$FLEET_GRPC" \
        EDGE_MTLS_CA="$CERT_DIR/fleet-ca.crt" \
        EDGE_MTLS_CERT="$CERT_DIR/$robot_id.crt" \
        EDGE_MTLS_KEY="$CERT_DIR/$robot_id.key" \
        EDGE_MTLS_SERVER_NAME=localhost \
        EDGE_FLEET_CA="$CERT_DIR/fleet-ca.crt" \
        EDGE_TASK_SOURCE=http \
        EDGE_TELEMETRY_INTERVAL=250ms \
        "$EDGE_WORKER"
}

start() {
    ensure_cloud
    load_env
    command -v "$CONDA_BIN" >/dev/null 2>&1 || die "conda is not available"
    [[ -f "$CERT_DIR/fleet-ca.crt" ]] || die "Fleet certificates are missing"
    (cd "$ROOT_DIR" && mkdir -p bin && go build -o "$EDGE_WORKER" ./cmd/edge-worker)
    local robocasa_python
    robocasa_python="$($CONDA_BIN run -n "$ROBOCASA_ENV_NAME" python -c 'import sys; print(sys.executable)' | tail -1)"
    [[ -x "$robocasa_python" ]] || die "could not resolve Python for Conda environment $ROBOCASA_ENV_NAME"
    assert_sim_ports_available

    launch "robocasa-runtime" "$LOG_DIR/robocasa-runtime.log" \
        PYTHONNOUSERSITE=1 \
        "$robocasa_python" -m tangying_robocasa.fleet_server \
        --sender-listen "127.0.0.1:$SIM_PORT_1" \
        --receiver-listen "127.0.0.1:$SIM_PORT_2" \
        --seed 7 --human-speed "${ROBOCASA_HUMAN_SPEED:-0.04}" \
        ${ROBOCASA_CHECKPOINT_PATH:+--checkpoint "$ROBOCASA_CHECKPOINT_PATH"}

    wait_for_port "$SIM_PORT_1" "robot-1 Runtime"
    wait_for_port "$SIM_PORT_2" "robot-2 Runtime"

    start_edge robot-1 "$SIM_PORT_1" "$(device_token_for robot-1)"
    start_edge robot-2 "$SIM_PORT_2" "$(device_token_for robot-2)"
    echo "robocasa-fleet: ready at $FLEET_URL/"
    echo "robocasa-fleet: run '$0 handoff'"
}

run_foreground() {
    trap stop EXIT INT TERM
    start
    echo "robocasa-fleet: foreground supervisor active; Ctrl-C stops only profile children"
    while is_running robocasa-runtime && is_running edge-robot-1 && is_running edge-robot-2; do
        sleep 2
    done
    die "a RoboCasa profile child stopped unexpectedly; inspect '$0 logs'"
}

stop_process() {
    local name="$1" pid
    is_running "$name" || return 0
    pid="$(<"$RUN_DIR/$name.pid")"
    echo "robocasa-fleet: stopping $name (pid $pid)"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.25
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$RUN_DIR/$name.pid"
}

stop() {
    stop_process edge-robot-2
    stop_process edge-robot-1
    stop_process robocasa-runtime
    if [[ -f "$RUN_DIR/started-cloud" ]]; then
        bash "$SCRIPT_DIR/fleet-up.sh" down
        rm -f "$RUN_DIR/started-cloud"
    fi
    echo "robocasa-fleet: stopped"
}

status() {
    local name
    for name in robocasa-runtime edge-robot-1 edge-robot-2; do
        if is_running "$name"; then
            echo "$name: running (pid $(<"$RUN_DIR/$name.pid"))"
        else
            echo "$name: stopped"
        fi
    done
    if cloud_healthy; then echo "fleet-cloud: healthy"; else echo "fleet-cloud: stopped"; fi
}

logs() {
    tail -f "$LOG_DIR"/robocasa-*.log
}

handoff() {
    load_env
    local token task_id state=""
    token="$(curl -fsS -k -X POST "$FLEET_URL/v1/auth/login" \
        -H 'Content-Type: application/json' \
        --data "{\"user\":\"$FLEET_OPERATOR_USER\",\"password\":\"$FLEET_OPERATOR_PASSWORD\"}" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
    task_id="$(curl -fsS -k -X POST "$FLEET_URL/v1/tasks" \
        -H 'Content-Type: application/json' -H "Authorization: Bearer $token" \
        --data '{"request":"让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区","adapter":"robocasa"}' \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
    curl -fsS -k -X POST "$FLEET_URL/v1/tasks/$task_id/approve" -H "Authorization: Bearer $token" >/dev/null
    echo "robocasa-fleet: task $task_id approved"
    for _ in $(seq 1 180); do
        state="$(curl -fsS -k "$FLEET_URL/v1/tasks/$task_id" -H "Authorization: Bearer $token" \
            | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')"
        case "$state" in SUCCEEDED|FAILED|CANCELLED) break ;; esac
        sleep 1
    done
    echo "robocasa-fleet: task $task_id final state: $state"
    [[ "$state" == "SUCCEEDED" ]] || return 1
}

case "${1:-start}" in
    start) start ;;
    run) run_foreground ;;
    stop) stop ;;
    status) status ;;
    logs) logs ;;
    handoff|demo) handoff ;;
    *) echo "Usage: scripts/robocasa-fleet.sh {run|start|handoff|status|logs|stop}" >&2; exit 2 ;;
esac
