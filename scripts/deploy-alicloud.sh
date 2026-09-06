#!/usr/bin/env bash
# Deploy an exact committed source tree. Site credentials never enter the archive.
set -Eeuo pipefail

DEPLOY_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
DEPLOY_HOST=${ALICLOUD_SSH_HOST:-}
DEPLOY_USER=${ALICLOUD_SSH_USER:-root}
DEPLOY_KEY=${ALICLOUD_SSH_KEY:-}
DEPLOY_REMOTE_DIR=${ALICLOUD_REMOTE_DIR:-/opt/tangying-robot-agent-os}

fail() { echo "deploy-alicloud: $*" >&2; exit 2; }
case "$DEPLOY_HOST" in ''|-*|*[!A-Za-z0-9._-]*) fail 'set ALICLOUD_SSH_HOST to a valid host name or IPv4 address';; esac
case "$DEPLOY_USER" in ''|-*|*[!A-Za-z0-9_-]*) fail 'invalid ALICLOUD_SSH_USER';; esac
case "$DEPLOY_REMOTE_DIR" in /|*..*|*[!A-Za-z0-9_./-]*) fail 'invalid ALICLOUD_REMOTE_DIR';; esac
[[ "$DEPLOY_REMOTE_DIR" == /* ]] || fail 'ALICLOUD_REMOTE_DIR must be absolute'

git -C "$DEPLOY_ROOT" diff --quiet || fail 'commit reviewed tracked changes before deploying'
git -C "$DEPLOY_ROOT" diff --cached --quiet || fail 'commit staged changes before deploying'
command -v go >/dev/null || fail 'Go is required to vendor the committed dependency set'

DEPLOY_STAGE=$(mktemp -d "${TMPDIR:-/tmp}/tangying-cloud.XXXXXX")
trap 'rm -rf "$DEPLOY_STAGE"' EXIT
mkdir "$DEPLOY_STAGE/source"
git -C "$DEPLOY_ROOT" archive HEAD | tar -xf - -C "$DEPLOY_STAGE/source"
# Build the vendor directory from committed go.mod/go.sum, never copy arbitrary
# untracked files, parent projects, certificates, .env, datasets or artifacts.
(cd "$DEPLOY_STAGE/source" && go mod vendor)
DEPLOY_PACKAGE="$DEPLOY_STAGE/source.tar.gz"
tar -czf "$DEPLOY_PACKAGE" -C "$DEPLOY_STAGE/source" .

SSH_OPTIONS=(-o StrictHostKeyChecking=yes)
[[ -z "$DEPLOY_KEY" ]] || SSH_OPTIONS+=(-i "$DEPLOY_KEY")
DEPLOY_TARGET="$DEPLOY_USER@$DEPLOY_HOST"
DEPLOY_UPLOAD="/tmp/tangying-cloud-${RANDOM}-$$.tar.gz"
ssh "${SSH_OPTIONS[@]}" "$DEPLOY_TARGET" "sudo mkdir -p '$DEPLOY_REMOTE_DIR' && sudo chown '$DEPLOY_USER' '$DEPLOY_REMOTE_DIR'"
scp "${SSH_OPTIONS[@]}" "$DEPLOY_PACKAGE" "$DEPLOY_TARGET:$DEPLOY_UPLOAD"
ssh "${SSH_OPTIONS[@]}" "$DEPLOY_TARGET" "tar -xzf '$DEPLOY_UPLOAD' -C '$DEPLOY_REMOTE_DIR' && rm '$DEPLOY_UPLOAD' && cd '$DEPLOY_REMOTE_DIR' && bash scripts/fleet-up.sh up --build"
echo "Deployed committed source to https://$DEPLOY_HOST/; verify health, device identity and persistent volumes before release."
echo 'Existing remote configuration is preserved. Inspect credentials only in a trusted terminal on the server.'
