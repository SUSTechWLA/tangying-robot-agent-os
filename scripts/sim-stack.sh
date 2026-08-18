#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

DEFAULT_ARTIFACTS_DIR="$ROOT_DIR/artifacts/sim-stack"
ARTIFACTS_DIR="${SIM_STACK_ARTIFACTS_DIR:-$DEFAULT_ARTIFACTS_DIR}"
SIM_PORT="${SIM_STACK_SIM_PORT:-50051}"
AGENT_PORT="${SIM_STACK_AGENT_PORT:-8787}"
SEED="${SIM_STACK_SEED:-7}"
SIM_PORT_EXPLICIT=0
AGENT_PORT_EXPLICIT=0
SEED_EXPLICIT=0
[[ -n "${SIM_STACK_SIM_PORT+x}" ]] && SIM_PORT_EXPLICIT=1
[[ -n "${SIM_STACK_AGENT_PORT+x}" ]] && AGENT_PORT_EXPLICIT=1
[[ -n "${SIM_STACK_SEED+x}" ]] && SEED_EXPLICIT=1
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
                --sim-port) SIM_PORT="$value"; SIM_PORT_EXPLICIT=1 ;;
                --agent-port) AGENT_PORT="$value"; AGENT_PORT_EXPLICIT=1 ;;
                --artifacts-dir) ARTIFACTS_DIR="$value" ;;
                --seed) SEED="$value"; SEED_EXPLICIT=1 ;;
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
LOCK_DIR="$RUN_DIR/lifecycle.lock"
LOCK_OWNER_FILE="$LOCK_DIR/owner"
STARTED_SIM_PID=""
STARTED_AGENT_PID=""
STARTUP_ACTIVE=0
LOCK_HELD=0
LOCK_OWNER_TOKEN=""
STACK_GENERATION=""
RECORDED_GENERATION=""
FOREGROUND_GENERATION=""

load_recorded_config() {
    RECORDED_GENERATION=""
    [[ -f "$METADATA_FILE" ]] || return 0
    local recorded_sim recorded_agent recorded_seed
    recorded_sim="$(sed -n 's/^SIM_PORT=//p' "$METADATA_FILE" | tail -1)"
    recorded_agent="$(sed -n 's/^AGENT_PORT=//p' "$METADATA_FILE" | tail -1)"
    recorded_seed="$(sed -n 's/^SEED=//p' "$METADATA_FILE" | tail -1)"
    RECORDED_GENERATION="$(sed -n 's/^GENERATION=//p' "$METADATA_FILE" | tail -1)"
    if [[ $SIM_PORT_EXPLICIT -eq 0 && -n "$recorded_sim" ]]; then
        SIM_PORT="$recorded_sim"
    fi
    if [[ $AGENT_PORT_EXPLICIT -eq 0 && -n "$recorded_agent" ]]; then
        AGENT_PORT="$recorded_agent"
    fi
    if [[ $SEED_EXPLICIT -eq 0 && -n "$recorded_seed" ]]; then
        SEED="$recorded_seed"
    fi
}

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
    if [[ "$ARTIFACTS_DIR" == *$'\n'* || "$ARTIFACTS_DIR" == *$'\r'* || "$ARTIFACTS_DIR" == *$'\t'* ]]; then
        die "artifacts directory contains a forbidden control character"
        return 1
    fi
}

process_command() {
    ps -ww -p "$1" -o command= 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' || true
}

process_birth() {
    local pid="$1"
    if [[ -r "/proc/$pid/stat" ]]; then
        local stat rest
        stat="$(<"/proc/$pid/stat")" || return 1
        rest="${stat##*) }"
        # starttime is field 22, or item 20 after removing pid and comm.
        set -- $rest
        [[ $# -ge 20 ]] || return 1
        printf 'linux:%s\n' "${20}"
        return 0
    fi
    local started
    started="$(ps -p "$pid" -o lstart= 2>/dev/null | awk '{$1=$1};1')"
    [[ -n "$started" ]] || return 1
    printf 'darwin:%s\n' "$started"
}

normalize_executable() {
    local path="$1"
    if command -v realpath >/dev/null 2>&1; then
        realpath "$path" 2>/dev/null
        return
    fi
    "$PYTHON" - "$path" <<'PY'
import os
import sys

print(os.path.realpath(sys.argv[1]))
PY
}

process_executable() {
    local pid="$1" path=""
    if [[ -e "/proc/$pid/exe" ]]; then
        path="$(readlink "/proc/$pid/exe" 2>/dev/null || true)"
    elif command -v lsof >/dev/null 2>&1; then
        path="$(lsof -a -p "$pid" -d txt -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
    fi
    if [[ -z "$path" ]]; then
        path="$(ps -ww -p "$pid" -o comm= 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' || true)"
    fi
    [[ -n "$path" ]] || return 1
    normalize_executable "$path"
}

identity_value() {
    local key="$1" file="$2"
    sed -n "s/^${key}=//p" "$file" | tail -1
}

recorded_process_state() {
    local pid_file="$1" identity_file="$2"
    [[ -f "$pid_file" && -f "$identity_file" ]] || return 1
    local pid expected_birth expected_executable expected_argv
    pid="$(tr -d '[:space:]' < "$pid_file")"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 2
    kill -0 "$pid" 2>/dev/null || return 1
    expected_birth="$(identity_value BIRTH "$identity_file")"
    expected_executable="$(identity_value EXECUTABLE "$identity_file")"
    expected_argv="$(identity_value ARGV "$identity_file")"
    [[ -n "$expected_birth" && -n "$expected_executable" && -n "$expected_argv" ]] || return 2
    # State 1 means the recorded process generation disappeared (including PID
    # reuse with a different birth). State 2 means the same birth is alive but
    # executable or argv no longer match and must never be signalled.
    [[ "$(process_birth "$pid" 2>/dev/null || true)" == "$expected_birth" ]] || return 1
    [[ "$(process_executable "$pid" 2>/dev/null || true)" == "$expected_executable" ]] || return 2
    [[ "$(process_command "$pid")" == "$expected_argv" ]] || return 2
    return 0
}

capture_process_identity() {
    local pid="$1" expected_executable="$2" expected_argv="$3"
    local deadline=$(( $(date +%s) + 2 )) birth executable argv
    while (( $(date +%s) <= deadline )); do
        if kill -0 "$pid" 2>/dev/null; then
            birth="$(process_birth "$pid" 2>/dev/null || true)"
            executable="$(process_executable "$pid" 2>/dev/null || true)"
            argv="$(process_command "$pid")"
            if [[ -n "$birth" && "$executable" == "$expected_executable" && "$argv" == "$expected_argv" ]]; then
                printf 'BIRTH=%s\nEXECUTABLE=%s\nARGV=%s\n' "$birth" "$executable" "$argv"
                return 0
            fi
        fi
        sleep 0.05
    done
    return 1
}

write_record() {
    local pid_file="$1" identity_file="$2" pid="$3" expected_executable="$4" expected_argv="$5"
    local pid_tmp="$pid_file.$$" identity_tmp="$identity_file.$$"
    if ! printf '%s\n' "$pid" > "$pid_tmp" \
        || ! capture_process_identity "$pid" "$expected_executable" "$expected_argv" > "$identity_tmp"; then
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
    [[ "$(tr -d '[:space:]' < "$pid_file")" == "$pid" ]] \
        && recorded_process_state "$pid_file" "$identity_file"
}

remove_record() {
    rm -f -- "$1" "$2"
}

lock_owner_is_live() {
    [[ -f "$LOCK_OWNER_FILE" ]] || return 1
    local owner_pid owner_birth
    owner_pid="$(identity_value PID "$LOCK_OWNER_FILE")"
    owner_birth="$(identity_value BIRTH "$LOCK_OWNER_FILE")"
    [[ "$owner_pid" =~ ^[0-9]+$ && -n "$owner_birth" ]] || return 1
    [[ "$(process_birth "$owner_pid" 2>/dev/null || true)" == "$owner_birth" ]]
}

lock_directory_age() {
    local modified now
    if modified="$(stat -f %m "$LOCK_DIR" 2>/dev/null)"; then
        :
    else
        modified="$(stat -c %Y "$LOCK_DIR" 2>/dev/null || echo 0)"
    fi
    now="$(date +%s)"
    printf '%s\n' "$(( now - modified ))"
}

discard_stale_lock() {
    local observed_owner="" current_owner="" stale="$RUN_DIR/lifecycle.lock.stale.$$"
    [[ -d "$LOCK_DIR" ]] || return 0
    if [[ -f "$LOCK_OWNER_FILE" ]]; then
        observed_owner="$(<"$LOCK_OWNER_FILE")"
        lock_owner_is_live && return 1
        current_owner="$(<"$LOCK_OWNER_FILE")"
        [[ "$current_owner" == "$observed_owner" ]] || return 1
    else
        (( $(lock_directory_age) >= 2 )) || return 1
    fi
    if mv "$LOCK_DIR" "$stale" 2>/dev/null; then
        rm -f -- "$stale/owner"
        rmdir "$stale" 2>/dev/null || true
        return 0
    fi
    return 1
}

release_lifecycle_lock() {
    [[ $LOCK_HELD -eq 1 ]] || return 0
    if [[ -f "$LOCK_OWNER_FILE" && "$(<"$LOCK_OWNER_FILE")" == "$LOCK_OWNER_TOKEN" ]]; then
        rm -f -- "$LOCK_OWNER_FILE"
        rmdir "$LOCK_DIR" 2>/dev/null || true
    fi
    LOCK_HELD=0
    LOCK_OWNER_TOKEN=""
}

lifecycle_lock_exit() {
    release_lifecycle_lock
}

lifecycle_lock_signal() {
    release_lifecycle_lock
    exit 130
}

acquire_lifecycle_lock() {
    if ! mkdir -p -- "$RUN_DIR"; then
        die "lifecycle lock directory cannot be created under $ARTIFACTS_DIR"
        return 1
    fi
    local deadline=$(( $(date +%s) + STARTUP_TIMEOUT )) owner_tmp owner_birth
    owner_birth="$(process_birth $$)" || {
        die "cannot determine lifecycle lock owner birth identity"
        return 1
    }
    LOCK_OWNER_TOKEN="PID=$$"$'\n'"BIRTH=$owner_birth"
    while (( $(date +%s) < deadline )); do
        if mkdir "$LOCK_DIR" 2>/dev/null; then
            owner_tmp="$LOCK_DIR/owner.$$"
            if printf '%s\n' "$LOCK_OWNER_TOKEN" > "$owner_tmp" \
                && mv "$owner_tmp" "$LOCK_OWNER_FILE"; then
                LOCK_HELD=1
                trap lifecycle_lock_exit EXIT
                trap lifecycle_lock_signal INT TERM
                return 0
            fi
            rm -f -- "$owner_tmp"
            rmdir "$LOCK_DIR" 2>/dev/null || true
            die "failed to record lifecycle lock owner"
            return 1
        fi
        if ! lock_owner_is_live; then
            discard_stale_lock || true
        fi
        sleep 0.05
    done
    die "timed out waiting for lifecycle lock under $RUN_DIR"
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
    local pid deadline state
    pid="$(tr -d '[:space:]' < "$pid_file")"
    # Revalidate immediately before every signal; PID, birth, executable, and
    # complete argv must all still match the committed identity.
    recorded_process_state "$pid_file" "$identity_file" || {
        echo "sim-stack: refusing TERM for $label: process identity changed" >&2
        return 1
    }
    if ! kill -TERM "$pid"; then
        echo "sim-stack: failed to signal $label with TERM" >&2
        return 1
    fi
    deadline=$(( $(date +%s) + STOP_TIMEOUT ))
    while (( $(date +%s) < deadline )); do
        recorded_process_state "$pid_file" "$identity_file"
        state=$?
        [[ $state -ne 0 ]] && break
        sleep 0.1
    done
    recorded_process_state "$pid_file" "$identity_file"
    state=$?
    if [[ $state -eq 2 ]]; then
        echo "sim-stack: refusing escalation for $label: same-birth process identity changed after TERM; retaining process record" >&2
        return 1
    fi
    if [[ $state -eq 0 ]]; then
        recorded_process_state "$pid_file" "$identity_file" || {
            echo "sim-stack: refusing KILL for $label: process identity changed" >&2
            return 1
        }
        if ! kill -KILL "$pid"; then
            echo "sim-stack: failed to signal $label with KILL" >&2
            return 1
        fi
        deadline=$(( $(date +%s) + STOP_TIMEOUT ))
        while (( $(date +%s) < deadline )); do
            recorded_process_state "$pid_file" "$identity_file"
            state=$?
            [[ $state -ne 0 ]] && break
            sleep 0.1
        done
        recorded_process_state "$pid_file" "$identity_file"
        state=$?
        if [[ $state -eq 2 ]]; then
            echo "sim-stack: $label changed identity after KILL; retaining process record" >&2
            return 1
        fi
        if [[ $state -eq 0 ]]; then
            echo "sim-stack: $label identity remained alive after KILL; retaining process record" >&2
            return 1
        fi
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
    release_lifecycle_lock
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
    if [[ $LOCK_HELD -eq 1 ]]; then
        load_recorded_config
        if [[ -n "$FOREGROUND_GENERATION" && "$RECORDED_GENERATION" == "$FOREGROUND_GENERATION" ]]; then
            stop_stack >/dev/null 2>&1 || true
        fi
        release_lifecycle_lock
    elif acquire_lifecycle_lock; then
        load_recorded_config
        if [[ -n "$FOREGROUND_GENERATION" && "$RECORDED_GENERATION" == "$FOREGROUND_GENERATION" ]]; then
            stop_stack >/dev/null 2>&1 || true
        fi
        release_lifecycle_lock
    fi
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
        printf 'GENERATION=%s\n' "$STACK_GENERATION"
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
        && "$(sed -n 's/^SEED=//p' "$METADATA_FILE")" == "$SEED" \
        && -n "$STACK_GENERATION" \
        && "$(sed -n 's/^GENERATION=//p' "$METADATA_FILE")" == "$STACK_GENERATION" ]]
}

new_generation() {
    printf '%s-%s-%s-%s\n' "$(date +%s)" "$$" "$RANDOM" "$RANDOM"
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

    local sim_argv="$PYTHON -m tangying_sim.server --listen 127.0.0.1:$SIM_PORT --seed $SEED"
    local sim_executable
    sim_executable="$(normalize_executable "$PYTHON")" || {
        startup_failure "failed to normalize MuJoCo executable"
        return 1
    }
    (
        cd "$ROOT_DIR" || exit 1
        exec "$PYTHON" -m tangying_sim.server --listen "127.0.0.1:$SIM_PORT" --seed "$SEED"
    ) >>"$SIM_LOG" 2>&1 &
    STARTED_SIM_PID=$!
    if ! write_record "$SIM_PID_FILE" "$SIM_IDENTITY_FILE" "$STARTED_SIM_PID" "$sim_executable" "$sim_argv"; then
        startup_failure "failed to atomically record MuJoCo PID and identity"
        return 1
    fi

    local agent_argv="$LOCAL_AGENT --dev-insecure --listen 127.0.0.1:$AGENT_PORT --robot 127.0.0.1:$SIM_PORT --data-dir $DATA_DIR"
    local agent_executable
    agent_executable="$(normalize_executable "$LOCAL_AGENT")" || {
        startup_failure "failed to normalize Local Agent executable"
        return 1
    }
    (
        cd "$ROOT_DIR" || exit 1
        exec "$LOCAL_AGENT" \
            --dev-insecure \
            --listen "127.0.0.1:$AGENT_PORT" \
            --robot "127.0.0.1:$SIM_PORT" \
            --data-dir "$DATA_DIR"
    ) >>"$AGENT_LOG" 2>&1 &
    STARTED_AGENT_PID=$!
    if ! write_record "$AGENT_PID_FILE" "$AGENT_IDENTITY_FILE" "$STARTED_AGENT_PID" "$agent_executable" "$agent_argv"; then
        startup_failure "failed to atomically record Local Agent PID and identity"
        return 1
    fi
    STACK_GENERATION="$(new_generation)"
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
        FOREGROUND_GENERATION="$STACK_GENERATION"
        trap foreground_cleanup EXIT
        trap foreground_signal INT TERM
        release_lifecycle_lock
    fi

    echo "Simulation stack started."
    echo "Console: http://127.0.0.1:$AGENT_PORT/"
    echo "MuJoCo log: $SIM_LOG"
    echo "Local Agent log: $AGENT_LOG"

    if [[ $FOREGROUND -eq 1 ]]; then
        wait "$STARTED_AGENT_PID"
        local foreground_result=$?
        foreground_cleanup
        return "$foreground_result"
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

run_locked_mutation() {
    local action="$1" result
    acquire_lifecycle_lock || return 1
    load_recorded_config
    if ! validate_options; then
        release_lifecycle_lock
        trap - EXIT INT TERM
        return 2
    fi
    "$action"
    result=$?
    release_lifecycle_lock
    trap - EXIT INT TERM
    return "$result"
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
    start) run_locked_mutation start_stack ;;
    stop) run_locked_mutation stop_stack ;;
    restart)
        run_locked_mutation restart_stack
        ;;
    status)
        load_recorded_config
        validate_options && status_stack
        ;;
    logs) logs_stack ;;
esac
