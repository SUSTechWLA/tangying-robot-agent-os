#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG=${1:-/etc/tangying-robot-agent-os/robot-pi.env}
ROOT=/opt/tangying-robot-agent-os

fail() {
  echo "FAIL $*" >&2
  exit 1
}

pass() {
  echo "PASS $*"
}

read_config() {
  key=$1
  awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); print; found=1} END {if (!found) exit 1}' "$CONFIG"
}

[ -r "$CONFIG" ] || fail "configuration is not readable: $CONFIG"
port1=$(read_config XLEROBOT_PORT1) || fail "XLEROBOT_PORT1 is missing"
port2=$(read_config XLEROBOT_PORT2) || fail "XLEROBOT_PORT2 is missing"
calibration=$(read_config XLEROBOT_CALIBRATION) || fail "XLEROBOT_CALIBRATION is missing"
server_key=$(read_config ROBOT_SERVER_KEY) || fail "ROBOT_SERVER_KEY is missing"
server_cert=$(read_config ROBOT_SERVER_CERT) || fail "ROBOT_SERVER_CERT is missing"
client_ca=$(read_config ROBOT_CLIENT_CA) || fail "ROBOT_CLIENT_CA is missing"

for device in "$port1" "$port2"; do
  [ -c "$device" ] || fail "serial device is unavailable: $device"
  [ -r "$device" ] && [ -w "$device" ] || fail "serial device is not readable and writable: $device"
done
pass "stable serial devices are available"

calibration_file="$calibration/tangying-xlerobot.json"
[ -s "$calibration_file" ] || fail "calibration is missing: $calibration_file"
pass "calibration file is present"

for path in "$server_key" "$server_cert" "$client_ca"; do
  [ -r "$path" ] || fail "mTLS file is not readable: $path"
done
openssl x509 -checkend 604800 -noout -in "$server_cert" >/dev/null \
  || fail "robot server certificate expires within seven days"
pass "mTLS material is present and current"

[ -x "$ROOT/.venv/bin/python" ] || fail "Robot Edge Python environment is missing"
"$ROOT/.venv/bin/python" -c \
  'import lerobot.robots.xlerobot_2wheels.xlerobot_2wheels; import tangying_robot_gateway' \
  || fail "XLeRobot or Robot Gateway Python integration cannot be imported"
pass "XLeRobot and Robot Gateway imports succeed"

pass "no-motion Robot Edge preflight complete"
