#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
elif [ "$#" -gt 0 ]; then
  echo "usage: scripts/robot-pi-quick-deploy.sh [--dry-run]" >&2
  exit 2
fi

[ "$(uname -s)" = "Linux" ] || { echo "run this on the Raspberry Pi" >&2; exit 2; }
case "$(uname -m)" in
  aarch64|arm64) ;;
  *) echo "expected arm64 Raspberry Pi, got $(uname -m)" >&2; exit 2 ;;
esac

export ROBOT_AGENT_DIRECT_EDGE="${ROBOT_AGENT_DIRECT_EDGE:-1}"

cd "$ROOT"
if [ "$DRY_RUN" = "1" ]; then
  ./install.sh robot-pi --dry-run --yes
  exit 0
fi

./install.sh robot-pi --yes

cat <<'EOF'

Robot Edge installation completed. Next steps on this Pi:

1. Record stable serial aliases and edit /etc/udev/rules.d/99-tangying-xlerobot.rules.
2. Run interactive calibration (this moves hardware):
     sudo -u tangying-robot /opt/tangying-robot-agent-os/.venv/bin/python \
       /opt/tangying-robot-agent-os/scripts/calibrate_xlerobot.py \
       --acknowledge-hardware-motion
3. Pair from the laptop:
     robot-agent pair xlerobot.local --ssh-user tangying-robot
4. Run no-motion preflight:
     sudo robot-agent doctor robot-pi
5. Complete docs/install/xlerobot-experiment.md before the first physical task.
EOF
