#!/usr/bin/env bash
# One-click start for every component in this repository.
#
#   ./scripts/start-all.sh up                       simulator + Local Agent + console
#   ./scripts/start-all.sh up --demo                ... then run the CLI task demo
#   ./scripts/start-all.sh up --with-navigation     ... plus RTAB-Map / Nav2
#   ./scripts/start-all.sh up --with-cloud --with-fleet-sim
#   ./scripts/start-all.sh status
#   ./scripts/start-all.sh logs [component]
#   ./scripts/start-all.sh down
#   ./scripts/start-all.sh check                    prerequisites only, starts nothing
#
# This script orchestrates; it does not reimplement. Every component is started
# by the lifecycle script that already owns it (sim-stack.sh, navigation-stack.sh,
# fleet-up.sh, fleet-sim.sh, demo.sh), and `down` stops exactly the components
# this script started, recorded in artifacts/start-all/state.
#
# Deployment targets, processes and ports are documented in docs/deployment.md.
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

ARTIFACTS_DIR="${START_ALL_ARTIFACTS_DIR:-$ROOT_DIR/artifacts/start-all}"
STATE_FILE="$ARTIFACTS_DIR/state"

OPERATION=""
WITH_NAVIGATION=0
WITH_CLOUD=0
WITH_FLEET_SIM=0
RUN_DEMO=0
SCENE="${START_ALL_SCENE:-tabletop}"
PERCEPTION="${START_ALL_PERCEPTION:-rgbd}"
SIM_PORT="${START_ALL_SIM_PORT:-50051}"
AGENT_PORT="${START_ALL_AGENT_PORT:-8787}"
NAVIGATION_MODE="${START_ALL_NAVIGATION_MODE:-mapping}"
LOG_COMPONENT="sim"

usage() {
    cat <<'EOF'
Usage: scripts/start-all.sh {up|down|status|logs|check} [options]

Operations:
  up        Start the components selected below, wait for each to report healthy,
            then print the addresses. Safe to re-run: every component start is
            idempotent.
  down      Stop exactly the components recorded by the last `up`.
  status    Show the current state of every component, whether or not this
            script started it.
  logs      Follow one component's logs (`logs [sim|navigation|cloud|fleet-sim]`,
            default sim).
  check     Verify prerequisites and print what `up` would start. Starts nothing.

Components:
  sim              MuJoCo world + Local Agent + console  (always on; robot/local)
  navigation       RTAB-Map / Nav2 container stack        (--with-navigation)
  cloud            Fleet control plane over Compose       (--with-cloud)
  fleet-sim        Two simulated edges dialling the cloud (--with-cloud --with-fleet-sim)
  demo             One CLI task run, then cleanup         (--demo, not persistent)

Options:
  --with-navigation        Also start the RTAB-Map / Nav2 stack; needs Docker.
  --with-cloud             Also start the Fleet cloud stack; needs Docker.
  --with-fleet-sim         Also start two simulated edges; needs --with-cloud.
  --demo                   Run `scripts/demo.sh` once after everything is up.
  --scene NAME             MuJoCo scene: tabletop (default), home, home_task.
  --perception MODE        rgbd (default) or ground-truth (legacy debug only).
  --sim-port PORT          MuJoCo gRPC port (default 50051).
  --agent-port PORT        Console port (default 8787).
  --navigation-mode MODE   mapping (default) or localization.
  -h, --help               Show this help.

Environment: START_ALL_SCENE, START_ALL_PERCEPTION, START_ALL_SIM_PORT,
START_ALL_AGENT_PORT, START_ALL_NAVIGATION_MODE, START_ALL_ARTIFACTS_DIR.

Only the sim component is started by default, because it is the only one that
needs no Docker and no real hardware. Cloud and navigation are opt-in so a
developer machine never gets a surprise listener on 443.
EOF
}

log() { printf 'start-all: %s\n' "$*"; }
die() { printf 'start-all: %s\n' "$*" >&2; exit 1; }

have_docker() {
    command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

# Components started by this script, one per line, newest last.
read_state() {
    [[ -f "$STATE_FILE" ]] || return 0
    grep -E '^[a-z-]+$' "$STATE_FILE" || true
}

record_started() {
    mkdir -p "$ARTIFACTS_DIR"
    printf '%s\n' "$1" >>"$STATE_FILE"
}

clear_state() {
    rm -f "$STATE_FILE"
}

component_status() {
    case "$1" in
        sim) bash "$SCRIPT_DIR/sim-stack.sh" status ;;
        navigation) bash "$SCRIPT_DIR/navigation-stack.sh" status ;;
        cloud) bash "$SCRIPT_DIR/fleet-up.sh" status ;;
        fleet-sim) bash "$SCRIPT_DIR/fleet-sim.sh" status ;;
        *) die "unknown component: $1" ;;
    esac
}

# Liveness probe. `fleet-up.sh status` and `fleet-sim.sh status` report state but
# always exit 0, so they cannot answer "is it running?" on their own.
component_running() {
    case "$1" in
        cloud)
            have_docker || return 1
            (cd "$ROOT_DIR/deploy/cloud" && docker compose ps --status running --quiet fleet-control-plane 2>/dev/null) | grep -q .
            ;;
        fleet-sim)
            component_status fleet-sim 2>/dev/null | grep -q ': running'
            ;;
        *)
            component_status "$1" >/dev/null 2>&1
            ;;
    esac
}

start_component() {
    case "$1" in
        sim)
            bash "$SCRIPT_DIR/sim-stack.sh" start \
                --perception "$PERCEPTION" --scene "$SCENE" \
                --sim-port "$SIM_PORT" --agent-port "$AGENT_PORT"
            ;;
        navigation)
            bash "$SCRIPT_DIR/navigation-stack.sh" start --mode "$NAVIGATION_MODE"
            ;;
        cloud)
            bash "$SCRIPT_DIR/fleet-up.sh" up
            ;;
        fleet-sim)
            bash "$SCRIPT_DIR/fleet-sim.sh" start
            ;;
        *) die "unknown component: $1" ;;
    esac
}

stop_component() {
    case "$1" in
        fleet-sim) bash "$SCRIPT_DIR/fleet-sim.sh" stop ;;
        cloud) bash "$SCRIPT_DIR/fleet-up.sh" down ;;
        navigation) bash "$SCRIPT_DIR/navigation-stack.sh" stop ;;
        sim) bash "$SCRIPT_DIR/sim-stack.sh" stop ;;
        *) die "unknown component: $1" ;;
    esac
}

check_prerequisites() {
    local failures=0
    if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
        log "missing .venv/bin/python — run 'make setup' first"
        failures=$((failures + 1))
    fi
    if [[ ! -x "$ROOT_DIR/bin/local-agent" ]]; then
        log "missing bin/local-agent — 'make build' will be run by 'up'"
    fi
    if [[ "$WITH_NAVIGATION$WITH_CLOUD$WITH_FLEET_SIM" != "000" ]] && ! have_docker; then
        log "Docker is required for --with-navigation/--with-cloud/--with-fleet-sim but is not usable"
        failures=$((failures + 1))
    fi
    if [[ "$WITH_FLEET_SIM" == "1" && "$WITH_CLOUD" != "1" ]]; then
        log "--with-fleet-sim needs --with-cloud: the edges dial the cloud control plane"
        failures=$((failures + 1))
    fi
    if [[ "$WITH_FLEET_SIM" == "1" && ! -f "$ROOT_DIR/deploy/cloud/.env" ]]; then
        log "missing deploy/cloud/.env — '--with-cloud' generates it before the edges start"
    fi
    if [[ "$failures" -gt 0 ]]; then
        return 1
    fi
    log "prerequisites: OK"
    return 0
}

describe_plan() {
    local plan="sim"
    [[ "$WITH_NAVIGATION" == "1" ]] && plan="$plan navigation"
    [[ "$WITH_CLOUD" == "1" ]] && plan="$plan cloud"
    [[ "$WITH_FLEET_SIM" == "1" ]] && plan="$plan fleet-sim"
    [[ "$RUN_DEMO" == "1" ]] && plan="$plan demo"
    log "components: $plan"
    log "sim: scene=$SCENE perception=$PERCEPTION sim-port=$SIM_PORT console=http://127.0.0.1:$AGENT_PORT/"
}

up() {
    check_prerequisites || die "prerequisites failed; nothing was started"
    describe_plan

    if [[ ! -x "$ROOT_DIR/bin/local-agent" ]]; then
        log "building binaries (make build)"
        make -C "$ROOT_DIR" build
    fi

    clear_state
    # Cloud first: simulated and real edges both dial it, so it must be
    # listening before they start their presence loop.
    for component in cloud sim navigation fleet-sim; do
        case "$component" in
            cloud) [[ "$WITH_CLOUD" == "1" ]] || continue ;;
            navigation) [[ "$WITH_NAVIGATION" == "1" ]] || continue ;;
            fleet-sim) [[ "$WITH_FLEET_SIM" == "1" ]] || continue ;;
        esac
        if component_running "$component"; then
            log "$component already running"
            record_started "$component"
            continue
        fi
        log "starting $component"
        start_component "$component"
        record_started "$component"
    done

    status

    if [[ "$RUN_DEMO" == "1" ]]; then
        log "running the task demo (this creates and approves a real task)"
        bash "$SCRIPT_DIR/demo.sh"
    fi

    log "ready. Stop everything with: ./scripts/start-all.sh down"
}

down() {
    local components=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && components+=("$line")
    done < <(read_state)
    if [[ "${#components[@]}" -eq 0 ]]; then
        log "nothing recorded in $STATE_FILE; every component is left as it is"
        log "start with './scripts/start-all.sh up' to make 'down' manage them"
        return 0
    fi
    # Reverse order: edges stop before the cloud they dial.
    local index
    for ((index = ${#components[@]} - 1; index >= 0; index--)); do
        local component="${components[$index]}"
        log "stopping $component"
        stop_component "$component" || log "$component did not stop cleanly"
    done
    clear_state
    log "stopped"
}

status() {
    local component
    for component in sim cloud navigation fleet-sim; do
        printf '\n--- %s ---\n' "$component"
        if component_running "$component"; then
            component_status "$component" || true
        else
            echo "not running"
        fi
    done
    printf '\nConsole: http://127.0.0.1:%s/\n' "$AGENT_PORT"
}

logs() {
    case "$LOG_COMPONENT" in
        sim) bash "$SCRIPT_DIR/sim-stack.sh" logs ;;
        navigation) bash "$SCRIPT_DIR/navigation-stack.sh" logs ;;
        cloud) bash "$SCRIPT_DIR/fleet-up.sh" logs ;;
        fleet-sim) bash "$SCRIPT_DIR/fleet-sim.sh" logs ;;
        *) die "unknown log component: $LOG_COMPONENT" ;;
    esac
}

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        up|down|status|logs|check)
            [[ -z "$OPERATION" ]] || die "only one operation may be given"
            OPERATION="$1"
            ;;
        --with-navigation) WITH_NAVIGATION=1 ;;
        --with-cloud) WITH_CLOUD=1 ;;
        --with-fleet-sim) WITH_FLEET_SIM=1 ;;
        --demo) RUN_DEMO=1 ;;
        --scene)
            shift; [[ "$#" -gt 0 ]] || die "--scene requires a value"; SCENE="$1" ;;
        --perception)
            shift; [[ "$#" -gt 0 ]] || die "--perception requires a value"; PERCEPTION="$1" ;;
        --sim-port)
            shift; [[ "$#" -gt 0 ]] || die "--sim-port requires a value"; SIM_PORT="$1" ;;
        --agent-port)
            shift; [[ "$#" -gt 0 ]] || die "--agent-port requires a value"; AGENT_PORT="$1" ;;
        --navigation-mode)
            shift; [[ "$#" -gt 0 ]] || die "--navigation-mode requires a value"; NAVIGATION_MODE="$1" ;;
        -h|--help) usage; exit 0 ;;
        sim|navigation|cloud|fleet-sim)
            # Bare component name is only meaningful for `logs`.
            if [[ "$OPERATION" == "logs" ]]; then LOG_COMPONENT="$1"; else die "unknown option: $1"; fi
            ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
    shift
done

if [[ -z "$OPERATION" ]]; then
    usage
    exit 2
fi

case "$OPERATION" in
    up) up ;;
    down) down ;;
    status) status ;;
    logs) logs ;;
    check) describe_plan; check_prerequisites ;;
esac
