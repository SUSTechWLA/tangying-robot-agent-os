#!/usr/bin/env bash
set -Eeuo pipefail

ROBOT_AGENT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
export ROBOT_AGENT_ROOT

usage() {
  cat <<'EOF'
Tangying Robot Agent OS installer

Usage:
  ./install.sh ROLE [options]

Roles:
  sim       complete MuJoCo development stack
  local     laptop Local Agent
  robot-pi  Raspberry Pi thin Robot Runtime

Options:
  --yes              accept package installation prompts
  --dry-run          print the mutation plan without changing the machine
  --version VERSION  record the requested release or branch
  -h, --help         show this help
EOF
}

role=""
ROBOT_AGENT_ASSUME_YES=0
ROBOT_AGENT_DRY_RUN=0
ROBOT_AGENT_VERSION=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    sim|local|robot-pi)
      if [ -n "$role" ]; then
        echo "error: only one install role may be selected" >&2
        exit 2
      fi
      role=$1
      ;;
    cloud)
		echo "error: cloud role was removed; install the local role on the user's laptop" >&2
		exit 2
		;;
    --yes)
      ROBOT_AGENT_ASSUME_YES=1
      ;;
    --dry-run)
      ROBOT_AGENT_DRY_RUN=1
      ;;
    --version)
      shift
      if [ "$#" -eq 0 ]; then
        echo "error: --version requires a value" >&2
        exit 2
      fi
      ROBOT_AGENT_VERSION=$1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ -z "$role" ]; then
  echo "error: install role is required" >&2
  usage >&2
  exit 2
fi

export ROBOT_AGENT_ASSUME_YES ROBOT_AGENT_DRY_RUN ROBOT_AGENT_VERSION
export ROBOT_AGENT_ROLE=$role

# shellcheck source=scripts/install/common.sh
. "$ROBOT_AGENT_ROOT/scripts/install/common.sh"

detect_platform
validate_role_platform "$role"
print_plan_header "$role"

case "$role" in
  sim) . "$ROBOT_AGENT_ROOT/scripts/install/sim.sh" ;;
  local) . "$ROBOT_AGENT_ROOT/scripts/install/local.sh" ;;
  robot-pi) . "$ROBOT_AGENT_ROOT/scripts/install/robot-pi.sh" ;;
esac

install_role
