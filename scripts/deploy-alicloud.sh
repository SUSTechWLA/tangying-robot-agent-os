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
"${SSH_CMD[@]}" "cd '$REMOTE_DIR/deploy/cloud' && if [ ! -f .env ]; then echo 'fleet: generating .env with strong secrets'; cp .env.example .env; OP=\$(openssl rand -hex 12); DT1=\$(openssl rand -hex 32); DT2=\$(openssl rand -hex 32); AS=\$(openssl rand -hex 32); RP=\$(openssl rand -hex 16); MP=\$(openssl rand -hex 16); sed -i.bak -e \"s/^FLEET_OPERATOR_PASSWORD=.*/FLEET_OPERATOR_PASSWORD=\$OP/\" -e \"s/^FLEET_DEVICE_CREDENTIALS=.*/FLEET_DEVICE_CREDENTIALS=robot-1:\$DT1,robot-2:\$DT2/\" -e \"s/^FLEET_AUTH_SECRET=.*/FLEET_AUTH_SECRET=\$AS/\" -e \"s/^MYSQL_ROOT_PASSWORD=.*/MYSQL_ROOT_PASSWORD=\$RP/\" -e \"s/^MYSQL_PASSWORD=.*/MYSQL_PASSWORD=\$MP/\" .env && rm -f .env.bak && chmod 600 .env && echo 'fleet: credentials stored in deploy/cloud/.env'; fi && bash scripts/fleet-certs.sh && echo 'allow all;' > allowed.conf && docker compose up -d --build"
echo "Fleet control plane deployed: https://$HOST/ (console) and :8444 (mTLS gRPC)"
echo "SSH to $HOST and read deploy/cloud/.env for the operator password and robot-specific device credentials."
echo "Check: ${SSH_CMD[*]} 'docker compose -f $REMOTE_DIR/deploy/cloud/docker-compose.yml ps'"
