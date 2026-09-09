#!/usr/bin/env bash
set -euo pipefail

# Home acceptance entrypoint. The normal lifecycle guarantees (PID identity,
# atomic metadata, rollback) remain in sim-stack.sh; this wrapper only fixes the
# commissioned scene and RGB-D observation mode.
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -lt 1 ]]; then
  echo "Usage: scripts/home-slam-stack.sh {start|stop|restart|status|logs} [sim-stack options]" >&2
  exit 2
fi
operation="$1"
shift
exec "$ROOT_DIR/scripts/sim-stack.sh" "$operation" "$@" --scene home --perception rgbd
