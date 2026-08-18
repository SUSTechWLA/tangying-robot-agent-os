#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

DEFAULT_ARTIFACTS_DIR="$ROOT_DIR/artifacts/sim-stack"
ARTIFACTS_DIR="${SIM_STACK_ARTIFACTS_DIR:-$DEFAULT_ARTIFACTS_DIR}"
SIM_PORT="${SIM_STACK_SIM_PORT:-50051}"
AGENT_PORT="${SIM_STACK_AGENT_PORT:-8787}"
SEED="${SIM_STACK_SEED:-7}"
STARTUP_TIMEOUT="${SIM_STACK_STARTUP_TIMEOUT:-20}"
STOP_TIMEOUT="${SIM_STACK_STOP_TIMEOUT:-5}"
PYTHON="${SIM_STACK_PYTHON:-$ROOT_DIR/.venv/bin/python}"
LOCAL_AGENT="${SIM_STACK_LOCAL_AGENT:-$ROOT_DIR/bin/local-agent}"
FOREGROUND=0
FOLLOW=0

usage() {
    cat <<'EOF'
Usage: scripts/sim-stack.sh {start|stop|restart|status|logs} [options]

Options:
  --foreground           Keep the stack attached to this terminal (start/restart).
  --background           Start detached (the default).
  --sim-port PORT        MuJoCo gRPC port (default: 50051).
  --agent-port PORT      Local Agent HTTP port (default: 8787).
  --artifacts-dir PATH   PID, log, and Local Agent data root.
  --seed SEED            MuJoCo scene seed (default: 7).
  --follow               Follow logs (logs only).

The same values can be set with SIM_STACK_SIM_PORT, SIM_STACK_AGENT_PORT,
SIM_STACK_ARTIFACTS_DIR, SIM_STACK_SEED, and SIM_STACK_STARTUP_TIMEOUT.
EOF
}

die() {
    echo "sim-stack: $*" >&2
    return 1
}

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi

OPERATION="$1"
shift
case "$OPERATION" in
    start|stop|restart|status|logs) ;;
    *) usage >&2; exit 2 ;;
esac

while [[ $# -gt 0 ]]; do
    case "$1" in
        --foreground)
            FOREGROUND=1
            shift
            ;;
        --background)
            FOREGROUND=0
            shift
            ;;
        --follow)
            FOLLOW=1
            shift
            ;;
        --sim-port|--agent-port|--artifacts-dir|--seed)
            if [[ $# -lt 2 ]]; then
                die "$1 requires a value"
                exit 2
            fi
            option="$1"
            value="$2"
            shift 2
            case "$option" in
                --sim-port) SIM_PORT="$value" ;;
                --agent-port) AGENT_PORT="$value" ;;
                --artifacts-dir) ARTIFACTS_DIR="$value" ;;
                --seed) SEED="$value" ;;
            esac
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1"
            exit 2
            ;;
    esac
done

RUN_DIR="$ARTIFACTS_DIR/run"
LOG_DIR="$ARTIFACTS_DIR/logs"
DATA_DIR="$ARTIFACTS_DIR/local-agent"
METADATA_FILE="$RUN_DIR/stack.env"
SIM_PID_FILE="$RUN_DIR/mujoco.pid"
SIM_IDENTITY_FILE="$RUN_DIR/mujoco.identity"
AGENT_PID_FILE="$RUN_DIR/local-agent.pid"
AGENT_IDENTITY_FILE="$RUN_DIR/local-agent.identity"
SIM_LOG="$LOG_DIR/mujoco.log"
AGENT_LOG="$LOG_DIR/local-agent.log"
STARTED_SIM_PID=""
STARTED_AGENT_PID=""
STARTUP_ACTIVE=0

load_recorded_ports() {
    [[ -f "$METADATA_FILE" ]] || return 0
    local recorded_sim recorded_agent
    recorded_sim="$(sed -n 's/^SIM_PORT=//p' "$METADATA_FILE" | tail -1)"
    recorded_agent="$(sed -n 's/^AGENT_PORT=//p' "$METADATA_FILE" | tail -1)"
    if [[ -z "${SIM_STACK_SIM_PORT+x}" && -n "$recorded_sim" ]]; then
        SIM_PORT="$recorded_sim"
    fi
    if [[ -z "${SIM_STACK_AGENT_PORT+x}" && -n "$recorded_agent" ]]; then
        AGENT_PORT="$recorded_agent"
    fi
}

if [[ "$OPERATION" != "start" && "$OPERATION" != "restart" ]]; then
    load_recorded_ports
fi

validate_number() {
    local label="$1" value="$2" minimum="$3" maximum="$4"
    if [[ ! "$value" =~ ^[0-9]+$ ]] || (( value < minimum || value > maximum )); then
        die "$label must be an integer between $minimum and $maximum"
        return 1
    fi
}

validate_options() {
    validate_number "simulation port" "$SIM_PORT" 1 65535 || return 1
    validate_number "Local Agent port" "$AGENT_PORT" 1 65535 || return 1
    validate_number "seed" "$SEED" 0 2147483647 || return 1
    validate_number "startup timeout" "$STARTUP_TIMEOUT" 1 600 || return 1
    validate_number "stop timeout" "$STOP_TIMEOUT" 1 60 || return 1
    if [[ "$SIM_PORT" == "$AGENT_PORT" ]]; then
        die "simulation and Local Agent ports must differ"
        return 1
    fi
}

process_command() {
    ps -p "$1" -o command= 2>/dev/null || true
}

recorded_process_state() {
    local pid_file="$1" identity_file="$2"
    [[ -f "$pid_file" && -f "$identity_file" ]] || return 1
    local pid identity command
    pid="$(tr -d '[:space:]' < "$pid_file")"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 2
    kill -0 "$pid" 2>/dev/null || return 1
    identity="$(<"$identity_file")"
    command="$(process_command "$pid")"
    [[ -n "$identity" && "$command" == *"$identity"* ]] || return 2
    return 0
}

write_record() {
    local pid_file="$1" identity_file="$2" pid="$3" identity="$4"
    local pid_tmp="$pid_file.$$" identity_tmp="$identity_file.$$"
    if ! printf '%s\n' "$pid" > "$pid_tmp" \
        || ! printf '%s\n' "$identity" > "$identity_tmp"; then
        rm -f -- "$pid_tmp" "$identity_tmp"
        return 1
    fi
    # The PID file is the commit marker: readers never see it before identity.
    if ! mv -f "$identity_tmp" "$identity_file"; then
        rm -f -- "$pid_tmp" "$identity_tmp"
        return 1
    fi
    if ! mv -f "$pid_tmp" "$pid_file"; then
        rm -f -- "$pid_tmp" "$identity_file"
        return 1
    fi
    [[ "$(tr -d '[:space:]' < "$pid_file")" == "$pid" \
        && "$(<"$identity_file")" == "$identity" ]]
}

remove_record() {
    rm -f -- "$1" "$2"
}

port_is_free() {
    "$PYTHON" - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket()
try:
    # Match gRPC/HTTP listener behavior while ignoring closed connections in
    # TIME_WAIT; a live foreign listener still makes this bind fail.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

wait_for_ports_free() {
    local deadline=$(( $(date +%s) + STOP_TIMEOUT ))
    while (( $(date +%s) < deadline )); do
        if port_is_free "$SIM_PORT" && port_is_free "$AGENT_PORT"; then
            return 0
        fi
        sleep 0.1
    done
    die "ports 127.0.0.1:$SIM_PORT and 127.0.0.1:$AGENT_PORT were not released within ${STOP_TIMEOUT}s"
}

runtime_ready() {
    "$PYTHON" - "127.0.0.1:$SIM_PORT" <<'PY' >/dev/null 2>&1
import sys

import grpc
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

channel = grpc.insecure_channel(sys.argv[1])
try:
    info = robot_pb2_grpc.RobotRuntimeStub(channel).GetRuntimeInfo(
        robot_pb2.GetRuntimeInfoRequest(), timeout=1
    )
    if info.adapter != "mujoco":
        raise SystemExit(1)
finally:
    channel.close()
PY
}

agent_ready() {
    curl -fsS --max-time 1 "http://127.0.0.1:$AGENT_PORT/healthz" >/dev/null 2>&1 \
        && agent_runtime_ready
}

agent_runtime_ready() {
    "$PYTHON" - "http://127.0.0.1:$AGENT_PORT/v1/runtime" <<'PY' >/dev/null 2>&1
import json
import sys
from urllib.request import urlopen

with urlopen(sys.argv[1], timeout=1) as response:
    runtime = json.load(response)
if runtime.get("Adapter") != "mujoco" or not runtime.get("Ready"):
    raise SystemExit(1)
PY
}

service_status() {
    local label="$1" pid_file="$2" identity_file="$3"
    recorded_process_state "$pid_file" "$identity_file"
    local result=$?
    if [[ $result -eq 0 ]]; then
        echo "$label process: running (pid $(tr -d '[:space:]' < "$pid_file"))"
        return 0
    fi
    if [[ $result -eq 2 ]]; then
        echo "$label process: identity mismatch" >&2
    else
        echo "$label process: stopped" >&2
    fi
    return 1
}

status_stack() {
    local failed=0
    service_status "MuJoCo" "$SIM_PID_FILE" "$SIM_IDENTITY_FILE" || failed=1
    service_status "Local-Agent" "$AGENT_PID_FILE" "$AGENT_IDENTITY_FILE" || failed=1
    if runtime_ready; then
        echo "MuJoCo endpoint: healthy (adapter mujoco, 127.0.0.1:$SIM_PORT)"
    else
        echo "MuJoCo endpoint: unhealthy (127.0.0.1:$SIM_PORT)" >&2
        failed=1
    fi
    if agent_ready; then
        echo "Local-Agent endpoint: healthy (http://127.0.0.1:$AGENT_PORT/)"
    else
        echo "Local-Agent endpoint: unhealthy (http://127.0.0.1:$AGENT_PORT/)" >&2
        failed=1
    fi
    return "$failed"
}

terminate_recorded() {
    local label="$1" pid_file="$2" identity_file="$3"
    if [[ ! -f "$pid_file" && ! -f "$identity_file" ]]; then
        return 0
    fi
    recorded_process_state "$pid_file" "$identity_file"
    local result=$?
    if [[ $result -ne 0 ]]; then
        if [[ $result -eq 1 ]]; then
            remove_record "$pid_file" "$identity_file"
            return 0
        fi
        echo "sim-stack: refusing to signal $label: recorded process identity does not match" >&2
        return 1
    fi
    local pid deadline
    pid="$(tr -d '[:space:]' < "$pid_file")"
    kill -TERM "$pid"
    deadline=$(( $(date +%s) + STOP_TIMEOUT ))
    while kill -0 "$pid" 2>/dev/null && (( $(date +%s) < deadline )); do
        sleep 0.1
    done
    if kill -0 "$pid" 2>/dev/null; then
        if ! recorded_process_state "$pid_file" "$identity_file"; then
            echo "sim-stack: refusing escalation for $label: process identity changed" >&2
            return 1
        fi
        kill -KILL "$pid"
        sleep 0.1
    fi
    remove_record "$pid_file" "$identity_file"
    echo "$label stopped"
}

stop_stack() {
    local failed=0
    terminate_recorded "Local Agent" "$AGENT_PID_FILE" "$AGENT_IDENTITY_FILE" || failed=1
    terminate_recorded "MuJoCo" "$SIM_PID_FILE" "$SIM_IDENTITY_FILE" || failed=1
    if [[ $failed -eq 0 ]]; then
        rm -f -- "$METADATA_FILE"
    fi
    return "$failed"
}

process_is_running() {
    local pid="$1" state
    kill -0 "$pid" 2>/dev/null || return 1
    state="$(ps -p "$pid" -o stat= 2>/dev/null || true)"
    [[ "$state" != Z* ]]
}

terminate_known_child() {
    local pid="$1"
    [[ -n "$pid" ]] || return 0
    process_is_running "$pid" || { wait "$pid" 2>/dev/null || true; return 0; }
    kill -TERM "$pid" 2>/dev/null || true
    local deadline=$(( $(date +%s) + STOP_TIMEOUT ))
    while process_is_running "$pid" && (( $(date +%s) < deadline )); do
        sleep 0.1
    done
    if process_is_running "$pid"; then
        kill -KILL "$pid" 2>/dev/null || true
    fi
    wait "$pid" 2>/dev/null || true
}

rollback_started_children() {
    STARTUP_ACTIVE=0
    trap - EXIT INT TERM
    terminate_known_child "$STARTED_AGENT_PID"
    terminate_known_child "$STARTED_SIM_PID"
    remove_record "$AGENT_PID_FILE" "$AGENT_IDENTITY_FILE"
    remove_record "$SIM_PID_FILE" "$SIM_IDENTITY_FILE"
    rm -f -- "$METADATA_FILE" "$METADATA_FILE.$$"
    STARTED_AGENT_PID=""
    STARTED_SIM_PID=""
}

startup_exit() {
    if [[ $STARTUP_ACTIVE -eq 1 ]]; then
        echo "sim-stack: interrupted startup; rollback known children" >&2
        rollback_started_children
    fi
}

startup_signal() {
    startup_exit
    exit 130
}

startup_failure() {
    echo "sim-stack: $*; rollback known children (logs: $LOG_DIR)" >&2
    rollback_started_children
    return 1
}

foreground_cleanup() {
    trap - EXIT INT TERM
    stop_stack >/dev/null 2>&1 || true
}

foreground_signal() {
    foreground_cleanup
    exit 130
}

wait_for_ready() {
    local deadline=$(( $(date +%s) + STARTUP_TIMEOUT ))
    while (( $(date +%s) < deadline )); do
        if recorded_process_state "$SIM_PID_FILE" "$SIM_IDENTITY_FILE" \
            && recorded_process_state "$AGENT_PID_FILE" "$AGENT_IDENTITY_FILE" \
            && runtime_ready && agent_ready; then
            return 0
        fi
        sleep 0.2
    done
    return 1
}

write_metadata() {
    local metadata_tmp="$METADATA_FILE.$$"
    if ! {
        printf 'SIM_PORT=%s\n' "$SIM_PORT"
        printf 'AGENT_PORT=%s\n' "$AGENT_PORT"
        printf 'SEED=%s\n' "$SEED"
    } > "$metadata_tmp"; then
        rm -f -- "$metadata_tmp"
        return 1
    fi
    if ! mv -f "$metadata_tmp" "$METADATA_FILE"; then
        rm -f -- "$metadata_tmp"
        return 1
    fi
    [[ "$(sed -n 's/^SIM_PORT=//p' "$METADATA_FILE")" == "$SIM_PORT" \
        && "$(sed -n 's/^AGENT_PORT=//p' "$METADATA_FILE")" == "$AGENT_PORT" \
        && "$(sed -n 's/^SEED=//p' "$METADATA_FILE")" == "$SEED" ]]
}

prepare_artifacts() {
    if ! mkdir -p -- "$RUN_DIR" "$LOG_DIR" "$DATA_DIR"; then
        die "artifacts directories cannot be created under $ARTIFACTS_DIR"
        return 1
    fi
    local directory
    for directory in "$RUN_DIR" "$LOG_DIR" "$DATA_DIR"; do
        if [[ ! -d "$directory" || ! -w "$directory" || ! -x "$directory" ]]; then
            die "artifacts directory is not writable: $directory"
            return 1
        fi
    done
    if ! : >> "$SIM_LOG" || ! : >> "$AGENT_LOG"; then
        die "simulation log files are not writable under $LOG_DIR"
        return 1
    fi
    local probe="$RUN_DIR/.write-probe.$$" committed="$RUN_DIR/.write-probe-committed.$$"
    if ! printf 'writable\n' > "$probe" || ! mv -f "$probe" "$committed"; then
        rm -f -- "$probe" "$committed"
        die "PID metadata cannot be atomically written under $RUN_DIR"
        return 1
    fi
    rm -f -- "$committed" || {
        die "PID metadata probe cannot be cleaned under $RUN_DIR"
        return 1
    }
}

start_stack() {
    if recorded_process_state "$SIM_PID_FILE" "$SIM_IDENTITY_FILE" \
        && recorded_process_state "$AGENT_PID_FILE" "$AGENT_IDENTITY_FILE" \
        && runtime_ready && agent_ready; then
        echo "Simulation stack is already running and healthy."
        echo "Console: http://127.0.0.1:$AGENT_PORT/"
        return 0
    fi

    for pair in "$SIM_PID_FILE|$SIM_IDENTITY_FILE" "$AGENT_PID_FILE|$AGENT_IDENTITY_FILE"; do
        local pid_file="${pair%%|*}" identity_file="${pair#*|}"
        recorded_process_state "$pid_file" "$identity_file"
        local state=$?
        if [[ $state -eq 0 ]]; then
            die "recorded stack is only partially healthy; run stop before start"
            return 1
        fi
        if [[ $state -eq 2 ]]; then
            die "recorded PID has a process identity mismatch; refusing to overwrite it"
            return 1
        fi
        remove_record "$pid_file" "$identity_file"
    done

    [[ -x "$PYTHON" ]] || { die "Python runtime is not executable: $PYTHON"; return 1; }
    [[ -x "$LOCAL_AGENT" ]] || { die "Local Agent binary is not executable: $LOCAL_AGENT (run make build)"; return 1; }
    command -v curl >/dev/null 2>&1 || { die "curl is required"; return 1; }
    if ! port_is_free "$SIM_PORT"; then
        die "port 127.0.0.1:$SIM_PORT is already occupied; no process was signalled"
        return 1
    fi
    if ! port_is_free "$AGENT_PORT"; then
        die "port 127.0.0.1:$AGENT_PORT is already occupied; no process was signalled"
        return 1
    fi

    prepare_artifacts || return 1

    STARTUP_ACTIVE=1
    trap startup_exit EXIT
    trap startup_signal INT TERM

    local sim_identity="$PYTHON -m tangying_sim.server --listen 127.0.0.1:$SIM_PORT --seed $SEED"
    (
        cd "$ROOT_DIR" || exit 1
        exec "$PYTHON" -m tangying_sim.server --listen "127.0.0.1:$SIM_PORT" --seed "$SEED"
    ) >>"$SIM_LOG" 2>&1 &
    STARTED_SIM_PID=$!
    if ! write_record "$SIM_PID_FILE" "$SIM_IDENTITY_FILE" "$STARTED_SIM_PID" "$sim_identity"; then
        startup_failure "failed to atomically record MuJoCo PID and identity"
        return 1
    fi

    local agent_identity="$LOCAL_AGENT --dev-insecure --listen 127.0.0.1:$AGENT_PORT --robot 127.0.0.1:$SIM_PORT --data-dir $DATA_DIR"
    (
        cd "$ROOT_DIR" || exit 1
        exec "$LOCAL_AGENT" \
            --dev-insecure \
            --listen "127.0.0.1:$AGENT_PORT" \
            --robot "127.0.0.1:$SIM_PORT" \
            --data-dir "$DATA_DIR"
    ) >>"$AGENT_LOG" 2>&1 &
    STARTED_AGENT_PID=$!
    if ! write_record "$AGENT_PID_FILE" "$AGENT_IDENTITY_FILE" "$STARTED_AGENT_PID" "$agent_identity"; then
        startup_failure "failed to atomically record Local Agent PID and identity"
        return 1
    fi
    if ! write_metadata; then
        startup_failure "failed to atomically record stack metadata"
        return 1
    fi

    if ! wait_for_ready; then
        startup_failure "services did not become ready within ${STARTUP_TIMEOUT}s"
        return 1
    fi

    STARTUP_ACTIVE=0
    trap - EXIT INT TERM

    if [[ $FOREGROUND -eq 1 ]]; then
        trap foreground_cleanup EXIT
        trap foreground_signal INT TERM
    fi

    echo "Simulation stack started."
    echo "Console: http://127.0.0.1:$AGENT_PORT/"
    echo "MuJoCo log: $SIM_LOG"
    echo "Local Agent log: $AGENT_LOG"

    if [[ $FOREGROUND -eq 1 ]]; then
        wait "$STARTED_AGENT_PID"
    fi
}

restart_stack() {
    stop_stack || return 1
    [[ -x "$PYTHON" ]] || { die "Python runtime is not executable: $PYTHON"; return 1; }
    wait_for_ports_free || return 1
    start_stack
}

logs_stack() {
    prepare_artifacts || return 1
    if [[ $FOLLOW -eq 1 ]]; then
        tail -n 100 -f "$SIM_LOG" "$AGENT_LOG"
    else
        tail -n 100 "$SIM_LOG" "$AGENT_LOG"
    fi
}

validate_options || exit 2
if [[ $FOREGROUND -eq 1 && "$OPERATION" != "start" && "$OPERATION" != "restart" ]]; then
    die "--foreground is valid only for start or restart"
    exit 2
fi
if [[ $FOLLOW -eq 1 && "$OPERATION" != "logs" ]]; then
    die "--follow is valid only for logs"
    exit 2
fi
case "$OPERATION" in
    start) start_stack ;;
    stop) stop_stack ;;
    restart)
        restart_stack
        ;;
    status) status_stack ;;
    logs) logs_stack ;;
esac
