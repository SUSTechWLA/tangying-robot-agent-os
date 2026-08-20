#!/usr/bin/env bash
set -euo pipefail

# Keep this environment reproducible even when the workstation has packages in
# ~/.local. Conda otherwise exposes those packages to Python and pip's resolver.
export PYTHONNOUSERSITE=1

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)

ROBOCASA_ENV_NAME="${ROBOCASA_ENV_NAME:-tangying-robocasa}"
ROBOCASA_ASSET_PROFILE="${ROBOCASA_ASSET_PROFILE:-minimal}"
ROBOCASA_ROOT="${ROBOCASA_ROOT:-$PROJECT_ROOT/datasets/robocasa}"
ROBOSUITE_ROOT="${ROBOSUITE_ROOT:-$PROJECT_ROOT/datasets/robosuite}"
ENV_MARKER="$PROJECT_ROOT/datasets/.tangying-robocasa-env-complete"
MINIMAL_MARKER="$PROJECT_ROOT/datasets/.tangying-robocasa-assets-minimal-complete"
FULL_MARKER="$PROJECT_ROOT/datasets/.tangying-robocasa-assets-full-complete"

if ! command -v conda >/dev/null 2>&1; then
  echo "robocasa-setup: conda is required" >&2
  exit 1
fi

if ! conda env list --json | python3 -c '
import json, os, sys
name = sys.argv[1]
paths = json.load(sys.stdin).get("envs", [])
raise SystemExit(0 if any(os.path.basename(path) == name for path in paths) else 1)
' "$ROBOCASA_ENV_NAME"; then
  conda create -y -n "$ROBOCASA_ENV_NAME" python=3.11
fi

mkdir -p "$PROJECT_ROOT/datasets"

if [ ! -d "$ROBOSUITE_ROOT/.git" ]; then
  if [ -e "$ROBOSUITE_ROOT" ]; then
    echo "robocasa-setup: $ROBOSUITE_ROOT exists but is not a git checkout" >&2
    exit 1
  fi
  git clone --depth 1 --filter=blob:none --branch master \
    https://github.com/ARISE-Initiative/robosuite.git "$ROBOSUITE_ROOT"
fi
git -C "$ROBOSUITE_ROOT" rev-parse --verify HEAD >/dev/null

if [ ! -d "$ROBOCASA_ROOT/.git" ]; then
  if [ -e "$ROBOCASA_ROOT" ]; then
    echo "robocasa-setup: $ROBOCASA_ROOT exists but is not a git checkout" >&2
    exit 1
  fi
  git clone --depth 1 --filter=blob:none https://github.com/robocasa/robocasa.git "$ROBOCASA_ROOT"
fi
git -C "$ROBOCASA_ROOT" rev-parse --verify HEAD >/dev/null

if [ ! -f "$ENV_MARKER" ] || ! conda run -n "$ROBOCASA_ENV_NAME" python -c \
  'import robocasa, robosuite, tangying_robocasa; from tangying_robot_proto.robot.v1 import robot_pb2' \
  >/dev/null 2>&1; then
  conda run -n "$ROBOCASA_ENV_NAME" python -m pip install --upgrade pip
  conda run -n "$ROBOCASA_ENV_NAME" python -m pip install -e "$ROBOSUITE_ROOT"
  conda run -n "$ROBOCASA_ENV_NAME" python -m pip install -e "$ROBOCASA_ROOT"
  conda run -n "$ROBOCASA_ENV_NAME" python -m pip install -e "$PROJECT_ROOT"
  touch "$ENV_MARKER"
fi

if [ ! -f "$ROBOSUITE_ROOT/robosuite/macros_private.py" ]; then
  conda run -n "$ROBOCASA_ENV_NAME" python -m robosuite.scripts.setup_macros
fi

if [ ! -f "$ROBOCASA_ROOT/robocasa/macros_private.py" ]; then
  conda run -n "$ROBOCASA_ENV_NAME" python -m robocasa.scripts.setup_macros
fi

download_assets() {
  local marker="$1"
  shift
  if [ -f "$marker" ]; then
    return
  fi
  printf 'y\n' | conda run --no-capture-output -n "$ROBOCASA_ENV_NAME" \
    python -m robocasa.scripts.download_kitchen_assets --type "$@"
  touch "$marker"
}

TEXTURE_MARKER="$PROJECT_ROOT/datasets/.tangying-robocasa-assets-tex-complete"
FIXTURE_MARKER="$PROJECT_ROOT/datasets/.tangying-robocasa-assets-fixtures-complete"
OBJECT_LW_MARKER="$PROJECT_ROOT/datasets/.tangying-robocasa-assets-objs-lw-complete"
if [ -f "$ROBOCASA_ROOT/robocasa/models/assets/textures/bricks/red_bricks.png" ] && \
   [ -f "$ROBOCASA_ROOT/robocasa/models/assets/textures/tiles/concrete_tiles.png" ]; then
  touch "$TEXTURE_MARKER"
fi
download_assets "$TEXTURE_MARKER" tex
download_assets "$FIXTURE_MARKER" fixtures_lw
download_assets "$OBJECT_LW_MARKER" objs_lw

if [ "$ROBOCASA_ASSET_PROFILE" = "full" ]; then
  download_assets "$PROJECT_ROOT/datasets/.tangying-robocasa-assets-tex-generative-complete" tex_generative
  download_assets "$PROJECT_ROOT/datasets/.tangying-robocasa-assets-objs-objaverse-complete" objs_objaverse
  download_assets "$PROJECT_ROOT/datasets/.tangying-robocasa-assets-objs-aigen-complete" objs_aigen
  touch "$FULL_MARKER"
elif [ "$ROBOCASA_ASSET_PROFILE" != "minimal" ]; then
  echo "robocasa-setup: ROBOCASA_ASSET_PROFILE must be minimal or full" >&2
  exit 1
fi

conda run -n "$ROBOCASA_ENV_NAME" python "$PROJECT_ROOT/scripts/robocasa-smoke.py"
touch "$MINIMAL_MARKER"
