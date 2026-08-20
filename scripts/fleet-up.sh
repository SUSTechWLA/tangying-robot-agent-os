#!/usr/bin/env bash
# One-click local Fleet cloud stack:
#
#   ./scripts/fleet-up.sh          start the full cloud (mysql + redis +
#                                  fleet-control-plane + nginx HTTPS/mTLS)
#   ./scripts/fleet-up.sh status   show container status
#   ./scripts/fleet-up.sh logs     follow control-plane logs
#   ./scripts/fleet-up.sh down     stop the stack
#   ./scripts/fleet-up.sh restart  rebuild + restart
#   ./scripts/fleet-up.sh env      print the generated secrets (operator and
#                                  robot-specific credentials) needed by edges
#
# The script generates deploy/cloud/.env (if missing), the mTLS certificate
# set, and the nginx client-IP whitelist, then starts Docker Compose and
# waits until the console answers on https://127.0.0.1/.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
CLOUD_DIR="$ROOT_DIR/deploy/cloud"
ENV_FILE="$CLOUD_DIR/.env"
ALLOWED_FILE="$CLOUD_DIR/allowed.conf"

usage() {
    cat <<'EOF'
Usage: scripts/fleet-up.sh {up|status|logs|down|restart|env} [--build]

Environment:
  FLEET_ROBOTS          comma-separated robot ids (default: robot-1,robot-2)
  FLEET_HTTPS_PORT      console HTTPS port (default: 443)
  FLEET_GRPC_PORT       mTLS gRPC passthrough port (default: 8444)
  FLEET_ALLOWED_CIDRS   nginx whitelist CIDRs or "all" (default: private ranges)
  FLEET_OPERATOR_USER   console login user (default: admin)
  FLEET_OPERATOR_PASSWORD console login password (default: generated)
  FLEET_DEVICE_CREDENTIALS robot-id:token pairs (default: generated per robot)
EOF
}

die() {
    echo "fleet-up: $*" >&2
    exit 1
}

generate_env() {
    if [[ -f "$ENV_FILE" ]]; then
        if ! grep -qE '^FLEET_DEVICE_CREDENTIALS=.+:.+' "$ENV_FILE"; then
            migrate_legacy_env
        fi
        persist_requested_world_id
        echo "fleet-up: using existing $ENV_FILE"
        return
    fi
    echo "fleet-up: generating $ENV_FILE"
    operator_pass="${FLEET_OPERATOR_PASSWORD:-$(openssl rand -hex 12)}"
    robots="${FLEET_ROBOTS:-robot-1,robot-2}"
    device_credentials="${FLEET_DEVICE_CREDENTIALS:-}"
    if [[ -z "$device_credentials" ]]; then
        IFS=',' read -ra robot_ids <<< "$robots"
        for robot_id in "${robot_ids[@]}"; do
            robot_id="$(echo "$robot_id" | xargs)"
            [[ -n "$robot_id" ]] || continue
            credential="$robot_id:$(openssl rand -hex 32)"
            if [[ -n "$device_credentials" ]]; then
                device_credentials="$device_credentials,$credential"
            else
                device_credentials="$credential"
            fi
        done
    fi
    auth_secret="$(openssl rand -hex 32)"
    root_password="$(openssl rand -hex 16)"
    mysql_password="$(openssl rand -hex 16)"
    cat > "$ENV_FILE" <<EOF
FLEET_OPERATOR_USER=${FLEET_OPERATOR_USER:-admin}
FLEET_OPERATOR_PASSWORD=$operator_pass
FLEET_DEVICE_CREDENTIALS=$device_credentials
FLEET_AUTH_SECRET=$auth_secret
FLEET_ROBOTS=$robots
FLEET_HTTPS_PORT=${FLEET_HTTPS_PORT:-443}
FLEET_GRPC_PORT=${FLEET_GRPC_PORT:-8444}
FLEET_ALLOWED_CIDRS=${FLEET_ALLOWED_CIDRS:-127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}
MYSQL_ROOT_PASSWORD=$root_password
MYSQL_DATABASE=fleet
MYSQL_USER=fleet
MYSQL_PASSWORD=$mysql_password
REDIS_STREAM=fleet.tasks.ready
REDIS_GROUP=fleet-control-plane
AGENT_PROVIDER=deterministic
FLEET_WORLD_ID=${FLEET_WORLD_ID:-fleet-default}
EOF
    chmod 600 "$ENV_FILE"
    echo "fleet-up: credentials stored in $ENV_FILE (mode 0600)"
    echo "fleet-up: run 'scripts/fleet-up.sh env' only in a trusted terminal to export them"
}

persist_requested_world_id() {
    [[ -n "${FLEET_WORLD_ID:-}" ]] || return 0
    local temporary
    temporary="$(mktemp "$ENV_FILE.world.XXXXXX")"
    awk '!/^FLEET_WORLD_ID=/' "$ENV_FILE" > "$temporary"
    echo "FLEET_WORLD_ID=$FLEET_WORLD_ID" >> "$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$ENV_FILE"
}

migrate_legacy_env() {
    grep -qE '^FLEET_DEVICE_TOKEN=.+' "$ENV_FILE" ||
        die "$ENV_FILE has neither FLEET_DEVICE_CREDENTIALS nor a legacy FLEET_DEVICE_TOKEN"
    local robots device_credentials="" robot_id credential temporary
    robots="$(grep -E '^FLEET_ROBOTS=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
    robots="${robots:-robot-1,robot-2}"
    IFS=',' read -ra robot_ids <<< "$robots"
    for robot_id in "${robot_ids[@]}"; do
        robot_id="$(echo "$robot_id" | xargs)"
        [[ -n "$robot_id" ]] || continue
        credential="$robot_id:$(openssl rand -hex 32)"
        if [[ -n "$device_credentials" ]]; then
            device_credentials="$device_credentials,$credential"
        else
            device_credentials="$credential"
        fi
    done
    [[ -n "$device_credentials" ]] || die "could not derive robot ids while migrating $ENV_FILE"
    temporary="$(mktemp "$ENV_FILE.migrate.XXXXXX")"
    awk '!/^FLEET_DEVICE_TOKEN=/ && !/^FLEET_DEVICE_CREDENTIALS=/' "$ENV_FILE" > "$temporary"
    echo "FLEET_DEVICE_CREDENTIALS=$device_credentials" >> "$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$ENV_FILE"
    echo "fleet-up: migrated legacy shared device token to robot-specific credentials"
}

generate_allowed_conf() {
    cidrs="$(grep -E '^FLEET_ALLOWED_CIDRS=' "$ENV_FILE" | cut -d= -f2-)"
    if [[ -z "$cidrs" ]]; then
        cidrs="all"
    fi
    if [[ "$cidrs" == "all" ]]; then
        echo "allow all;" > "$ALLOWED_FILE"
    else
        : > "$ALLOWED_FILE"
        IFS=',' read -ra parts <<< "$cidrs"
        for part in "${parts[@]}"; do
            echo "allow $(echo "$part" | xargs);" >> "$ALLOWED_FILE"
        done
        echo "deny all;" >> "$ALLOWED_FILE"
    fi
    echo "fleet-up: nginx whitelist -> $ALLOWED_FILE"
}

ensure_docker() {
    docker version >/dev/null 2>&1 || die "docker is not running"
    docker compose version >/dev/null 2>&1 || die "docker compose plugin is missing"
}

up() {
    ensure_docker
    generate_env
    bash "$SCRIPT_DIR/fleet-certs.sh"
    generate_allowed_conf
    local build_flag=""
    if [[ "${1:-}" == "--build" ]]; then
        build_flag="--build"
    fi
    (cd "$CLOUD_DIR" && docker compose up -d $build_flag)
    echo "fleet-up: waiting for the console on https://127.0.0.1:${FLEET_HTTPS_PORT:-443}/ ..."
    local attempts=0
    while [[ $attempts -lt 60 ]]; do
        if curl -fsS -k "https://127.0.0.1:${FLEET_HTTPS_PORT:-443}/healthz" >/dev/null 2>&1; then
            echo "fleet-up: cloud is healthy"
            print_summary
            return 0
        fi
        attempts=$((attempts + 1))
        sleep 2
    done
    die "cloud did not become healthy within 120s; see 'scripts/fleet-up.sh logs'"
}

print_summary() {
    local https_port="${FLEET_HTTPS_PORT:-443}"
    local grpc_port="${FLEET_GRPC_PORT:-8444}"
    echo ""
    echo "============================ Fleet Cloud ============================"
    echo " Console (login required):   https://127.0.0.1:${https_port}/"
    echo " Local browser console:      http://127.0.0.1:${FLEET_LOOPBACK_HTTP_PORT:-18080}/ (loopback only)"
    echo " mTLS gRPC robot channel:    127.0.0.1:${grpc_port}  (TCP passthrough)"
    echo " Internal control plane:     :8080 (NOT exposed outside Docker)"
    echo " Operator user:             $(grep -E '^FLEET_OPERATOR_USER=' "$ENV_FILE" | cut -d= -f2-)"
    echo " Credentials:               stored in deploy/cloud/.env (not printed)"
    echo " Next: bash scripts/fleet-sim.sh   (two MuJoCo robots + edge workers)"
    echo "====================================================================="
}

status() {
    (cd "$CLOUD_DIR" && docker compose ps)
}

logs() {
    (cd "$CLOUD_DIR" && docker compose logs -f --tail=100 fleet-control-plane)
}

down() {
    ensure_docker
    (cd "$CLOUD_DIR" && docker compose down)
}

env_print() {
    [[ -f "$ENV_FILE" ]] || die "no $ENV_FILE yet; run 'scripts/fleet-up.sh up' first"
    echo "export FLEET_OPERATOR_USER=$(grep -E '^FLEET_OPERATOR_USER=' "$ENV_FILE" | cut -d= -f2-)"
    echo "export FLEET_OPERATOR_PASSWORD=$(grep -E '^FLEET_OPERATOR_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
    echo "export FLEET_DEVICE_CREDENTIALS=$(grep -E '^FLEET_DEVICE_CREDENTIALS=' "$ENV_FILE" | cut -d= -f2-)"
}

OPERATION="${1:-up}"
shift || true
case "$OPERATION" in
    up) up "${1:-}" ;;
    status) status ;;
    logs) logs ;;
    down) down ;;
    restart) down && up --build ;;
    env) env_print ;;
    --help|-h) usage ;;
    *) usage >&2; exit 2 ;;
esac
