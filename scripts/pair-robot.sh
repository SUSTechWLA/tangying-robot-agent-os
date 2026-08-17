#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)

die() {
  echo "error: $*" >&2
  exit 1
}

usage() {
  echo "Usage: robot-agent pair ROBOT_HOST [--ssh-user USER] [--new-ca]"
}

[ "$#" -gt 0 ] || { usage >&2; exit 2; }
ROBOT_HOST=$1
shift
SSH_USER=ubuntu
NEW_CA=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ssh-user)
      shift
      [ "$#" -gt 0 ] || die "--ssh-user requires a value"
      SSH_USER=$1
      ;;
    --new-ca) NEW_CA=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown pairing option: $1" ;;
  esac
  shift
done

case "$ROBOT_HOST" in
  *[!A-Za-z0-9._-]*|'') die "robot host contains unsupported characters" ;;
esac
case "$SSH_USER" in
  *[!A-Za-z0-9._-]*|'') die "SSH user contains unsupported characters" ;;
esac

command -v openssl >/dev/null 2>&1 || die "openssl is required"
if [ "${ROBOT_AGENT_TEST_MODE:-0}" != "1" ]; then
  command -v ssh >/dev/null 2>&1 || die "ssh is required"
  command -v scp >/dev/null 2>&1 || die "scp is required"
fi

if [ -n "${ROBOT_AGENT_STATE_DIR:-}" ]; then
  STATE_DIR=$ROBOT_AGENT_STATE_DIR
elif [ "$(uname -s)" = "Darwin" ]; then
  STATE_DIR="$HOME/Library/Application Support/TangyingRobotAgent"
else
  STATE_DIR="$HOME/.local/share/tangying-robot-agent-os"
fi

if [ -n "${ROBOT_AGENT_CONFIG_DIR:-}" ]; then
  CONFIG_DIR=$ROBOT_AGENT_CONFIG_DIR
elif [ "$(uname -s)" = "Darwin" ]; then
  CONFIG_DIR=$STATE_DIR
else
  CONFIG_DIR="$HOME/.config/tangying-robot-agent-os"
fi

CERT_DIR="$STATE_DIR/certs"
mkdir -p "$CERT_DIR" "$CONFIG_DIR"
chmod 0700 "$STATE_DIR" "$CERT_DIR" "$CONFIG_DIR"

resolve_robot_ip() {
  if [ "${ROBOT_AGENT_TEST_MODE:-0}" = "1" ] && [ -n "${ROBOT_AGENT_PAIR_IP:-}" ]; then
    echo "$ROBOT_AGENT_PAIR_IP"
    return
  fi
  if command -v getent >/dev/null 2>&1; then
    getent ahostsv4 "$ROBOT_HOST" | awk 'NR==1 {print $1}'
    return
  fi
  if command -v dscacheutil >/dev/null 2>&1; then
    dscacheutil -q host -a name "$ROBOT_HOST" | awk '/ip_address:/ {print $2; exit}'
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import socket,sys; print(socket.gethostbyname(sys.argv[1]))' "$ROBOT_HOST"
    return
  fi
  die "cannot resolve robot IP; install getent or Python 3"
}

ROBOT_IP=$(resolve_robot_ip)
[ -n "$ROBOT_IP" ] || die "could not resolve IPv4 address for $ROBOT_HOST"
case "$ROBOT_IP" in
  *[!0-9.]*|'') die "resolved robot address is not IPv4: $ROBOT_IP" ;;
esac

CA_KEY="$CERT_DIR/ca.key"
CA_CERT="$CERT_DIR/ca.crt"
if [ "$NEW_CA" = "1" ]; then
  backup="$CERT_DIR/ca-backup-$(date -u '+%Y%m%dT%H%M%SZ')"
  mkdir -p "$backup"
  chmod 0700 "$backup"
  [ ! -e "$CA_KEY" ] || cp -p "$CA_KEY" "$backup/ca.key"
  [ ! -e "$CA_CERT" ] || cp -p "$CA_CERT" "$backup/ca.crt"
  rm -f "$CA_KEY" "$CA_CERT"
fi

if [ ! -s "$CA_KEY" ] || [ ! -s "$CA_CERT" ]; then
  openssl ecparam -name prime256v1 -genkey -noout -out "$CA_KEY"
  openssl req -x509 -new -sha256 -key "$CA_KEY" -out "$CA_CERT" -days 3650 \
    -subj '/CN=Tangying Robot Local CA/O=Tangying Robot Agent OS'
fi
chmod 0600 "$CA_KEY"
chmod 0644 "$CA_CERT"

work=$(mktemp -d "${TMPDIR:-/tmp}/tangying-pair.XXXXXX")
cleanup() {
  rm -rf "$work"
}
trap cleanup EXIT INT TERM

issue_leaf() {
  name=$1
  common_name=$2
  usage=$3
  san=$4
  key="$CERT_DIR/$name.key"
  certificate="$CERT_DIR/$name.crt"
  openssl ecparam -name prime256v1 -genkey -noout -out "$key"
  openssl req -new -sha256 -key "$key" -out "$work/$name.csr" -subj "/CN=$common_name/O=Tangying Robot Agent OS"
  {
    echo "basicConstraints=critical,CA:FALSE"
    echo "keyUsage=critical,digitalSignature,keyAgreement"
    echo "extendedKeyUsage=$usage"
    echo "subjectAltName=$san"
  } >"$work/$name.ext"
  serial="0x$(openssl rand -hex 16)"
  openssl x509 -req -sha256 -in "$work/$name.csr" -CA "$CA_CERT" -CAkey "$CA_KEY" \
    -set_serial "$serial" -days 90 -extfile "$work/$name.ext" -out "$certificate" >/dev/null 2>&1
  chmod 0600 "$key"
  chmod 0644 "$certificate"
  openssl verify -CAfile "$CA_CERT" "$certificate" >/dev/null
}

issue_leaf local-agent tangying-local-agent clientAuth 'DNS:tangying-local-agent'
issue_leaf server "$ROBOT_HOST" serverAuth "DNS:$ROBOT_HOST,IP:$ROBOT_IP"

deploy_local_fixture() {
  remote_certs="$ROBOT_AGENT_PAIR_LOCAL_ROOT/var/lib/tangying-robot-agent-os/certs"
  mkdir -p "$remote_certs"
  chmod 0700 "$remote_certs"
  install -m 0600 "$CERT_DIR/server.key" "$remote_certs/server.key"
  install -m 0644 "$CERT_DIR/server.crt" "$remote_certs/server.crt"
  install -m 0644 "$CA_CERT" "$remote_certs/client-ca.crt"
}

deploy_over_ssh() {
  target="$SSH_USER@$ROBOT_HOST"
  stage="/tmp/tangying-robot-pair-$RANDOM"
  ssh "$target" "umask 077 && mkdir -p '$stage'"
  scp "$CERT_DIR/server.key" "$CERT_DIR/server.crt" "$CA_CERT" "$target:$stage/"
  ssh "$target" "sudo install -d -o tangying-robot -g tangying-robot -m 0700 /var/lib/tangying-robot-agent-os/certs && sudo install -o tangying-robot -g tangying-robot -m 0600 '$stage/server.key' /var/lib/tangying-robot-agent-os/certs/server.key && sudo install -o tangying-robot -g tangying-robot -m 0644 '$stage/server.crt' /var/lib/tangying-robot-agent-os/certs/server.crt && sudo install -o tangying-robot -g tangying-robot -m 0644 '$stage/ca.crt' /var/lib/tangying-robot-agent-os/certs/client-ca.crt && rm -rf '$stage' && sudo systemctl restart tangying-robot-edge.service"
}

if [ "${ROBOT_AGENT_TEST_MODE:-0}" = "1" ] && [ -n "${ROBOT_AGENT_PAIR_LOCAL_ROOT:-}" ]; then
  deploy_local_fixture
else
  deploy_over_ssh
fi

set_config() {
  key=$1
  value=$2
  path="$CONFIG_DIR/local.env"
  temporary="$path.tmp.$$"
  if [ -f "$path" ]; then
    awk -v wanted="$key" -v replacement="$value" '
      BEGIN { found = 0 }
      index($0, wanted "=") == 1 { print wanted "=" replacement; found = 1; next }
      { print }
      END { if (!found) print wanted "=" replacement }
    ' "$path" >"$temporary"
  else
    printf '%s=%s\n' "$key" "$value" >"$temporary"
  fi
  chmod 0600 "$temporary"
  mv "$temporary" "$path"
}

set_config ROBOT_ADDRESS "$ROBOT_HOST:50051"
set_config ROBOT_SERVER_NAME "$ROBOT_HOST"
set_config ROBOT_CA "$CA_CERT"
set_config ROBOT_CERT "$CERT_DIR/local-agent.crt"
set_config ROBOT_KEY "$CERT_DIR/local-agent.key"

echo "pairing complete: robot=$ROBOT_HOST ip=$ROBOT_IP client=$CERT_DIR/local-agent.crt"
echo "CA private key remains local: $CA_KEY"
