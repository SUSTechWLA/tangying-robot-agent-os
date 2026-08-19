#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
HOST=${ALICLOUD_SSH_HOST:-}
USER=${ALICLOUD_SSH_USER:-root}
KEY=${ALICLOUD_SSH_KEY:-}
REMOTE_DIR=${ALICLOUD_REMOTE_DIR:-/opt/tangying-robot-agent-os}

if [ -z "$HOST" ]; then
  echo "usage: ALICLOUD_SSH_HOST=1.2.3.4 [ALICLOUD_SSH_USER=root] [ALICLOUD_SSH_KEY=~/.ssh/id_rsa] $0" >&2
  exit 2
fi

SSH_OPTS=(-o StrictHostKeyChecking=accept-new)
if [ -n "$KEY" ]; then
  SSH_OPTS+=(-i "$KEY")
fi
SSH_CMD=(ssh "${SSH_OPTS[@]}" "$USER@$HOST")

PACKAGE=/tmp/tangying-robot-agent-os-cloud.tar.gz
tar --exclude='.git' --exclude='.venv' --exclude='XLeRobot' --exclude='artifacts' --exclude='logs' \
  -czf "$PACKAGE" -C "$ROOT" .

"${SSH_CMD[@]}" "sudo mkdir -p '$REMOTE_DIR' && sudo chown -R '$USER' '$REMOTE_DIR'"
scp "${SSH_OPTS[@]}" "$PACKAGE" "$USER@$HOST:/tmp/tangying-robot-agent-os-cloud.tar.gz"
"${SSH_CMD[@]}" "tar -xzf /tmp/tangying-robot-agent-os-cloud.tar.gz -C '$REMOTE_DIR' && rm /tmp/tangying-robot-agent-os-cloud.tar.gz"
"${SSH_CMD[@]}" "cd '$REMOTE_DIR/deploy/cloud' && cp -n .env.example .env || true && docker compose up -d --build"
echo "Fleet control plane deployed to http://$HOST:8080"
echo "Check: ${SSH_CMD[*]} 'docker compose -f $REMOTE_DIR/deploy/cloud/docker-compose.yml ps'"
