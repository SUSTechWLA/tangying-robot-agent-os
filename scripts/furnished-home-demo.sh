#!/usr/bin/env bash
# Prepare licensed household assets and run the existing RGB-D robot services.
set -euo pipefail
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_PYTHON="${SIM_STACK_PYTHON:-$ROOT_DIR/.venv/bin/python}"
DEMO_PACK="$ROOT_DIR/artifacts/sim-assets/furnished-home"
export SIM_STACK_ARTIFACTS_DIR="${SIM_STACK_ARTIFACTS_DIR:-$ROOT_DIR/artifacts/sim-stack/furnished-home}"
export TANGYING_MAP_ROOT="${TANGYING_MAP_ROOT:-$ROOT_DIR/artifacts/maps/furnished-home}"
export TANGYING_SIM_CALIBRATION_DIR="${TANGYING_SIM_CALIBRATION_DIR:-$ROOT_DIR/artifacts/calibration/furnished-home}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Usage: scripts/furnished-home-demo.sh [start|restart] [sim-stack options]

Prepare the pinned open-source household meshes, then launch the current robot
with RGB-D perception, mapping services and room/workspace camera views.
Default operation is start. Use restart to reset an existing episode explicitly.
Pass --sim-port, --agent-port, --artifacts-dir as for scripts/sim-stack.sh.
To collect a saved map, use the console's SLAM page or scripts/build_sim_map.py.
EOF
    exit 0
fi
DEMO_OPERATION="start"
if [[ "${1:-}" == "start" || "${1:-}" == "restart" ]]; then
    DEMO_OPERATION="$1"
    shift
fi
cd "$ROOT_DIR"
"$DEMO_PYTHON" -c 'import collada' >/dev/null 2>&1 || {
    echo "Install conversion dependencies first: .venv/bin/pip install -e '.[visual]'" >&2
    exit 1
}
if [[ ! -d "$ROOT_DIR/artifacts/sim-assets/aws-small-house" ]]; then
    "$DEMO_PYTHON" scripts/prepare_home_world.py --output "$ROOT_DIR/artifacts/sim-assets"
fi
"$DEMO_PYTHON" scripts/prepare_furnished_home.py --output "$DEMO_PACK" > "$ROOT_DIR/artifacts/sim-assets/furnished-home-preparation.json"
exec bash scripts/sim-stack.sh "$DEMO_OPERATION" "$@" --scene home_task --perception rgbd --home-assets "$DEMO_PACK"
