#!/usr/bin/env bash
# Cold-start precheck: can this machine host the role you are about to install?
#
#   ./scripts/precheck.sh              # check every role, this machine
#   ./scripts/precheck.sh local        # check one role
#   ./scripts/precheck.sh sim robot-pi
#
# Why this exists: install.sh validates the platform and dies with
# "unsupported platform for <role>", which tells you that you failed but not
# what this machine has or how far off it is. On a fresh machine that is the
# difference between one command and an afternoon.
#
# Design rules, both learned from the rest of this repository:
#
#   * It only reads. It installs nothing, changes no configuration, and starts
#     no service. A precheck that fixes things cannot be run to find out.
#   * A missing optional tool is a WARN, not a FAIL. Docker is only needed for
#     the cloud stack, and a robot build is not a cloud build. Reporting
#     optional gaps as failures is how a check stops being read.
#
# Exit codes: 0 = every required check passed, 1 = at least one required check
# failed, 2 = usage error.

set -uo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

FAILURES=0
WARNINGS=0
PASSES=0

# --- reporting -------------------------------------------------------------

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_PASS=$'\033[32m'; C_FAIL=$'\033[31m'; C_WARN=$'\033[33m'; C_HEAD=$'\033[1m'; C_OFF=$'\033[0m'
else
    C_PASS=''; C_FAIL=''; C_WARN=''; C_HEAD=''; C_OFF=''
fi

pass()  { printf '  %s PASS %s %s\n' "$C_PASS" "$C_OFF" "$1"; PASSES=$((PASSES + 1)); }
fail()  { printf '  %s FAIL %s %s\n' "$C_FAIL" "$C_OFF" "$1"; FAILURES=$((FAILURES + 1)); }
warn()  { printf '  %s WARN %s %s\n' "$C_WARN" "$C_OFF" "$1"; WARNINGS=$((WARNINGS + 1)); }
head()  { printf '\n%s== %s ==%s\n' "$C_HEAD" "$1" "$C_OFF"; }
note()  { printf '       %s\n' "$1"; }

usage() {
    cat <<'EOF'
Usage: scripts/precheck.sh [role ...]

Roles: sim, local, robot-pi, cloud
With no role, every role is checked.

This script only reads the machine. It installs nothing and changes nothing.
EOF
}

# --- platform detection ----------------------------------------------------

detect_platform() {
    uname_s="$(uname -s)"
    case "$uname_s" in
        Darwin) OS_FAMILY=darwin; DISTRO=macos ;;
        Linux)
            OS_FAMILY=linux
            if [ -r /etc/os-release ]; then
                # shellcheck disable=SC1091
                DISTRO="$(. /etc/os-release && printf '%s' "${ID:-unknown}")"
                DISTRO_VERSION="$(. /etc/os-release && printf '%s' "${VERSION_ID:-unknown}")"
            else
                DISTRO=unknown; DISTRO_VERSION=unknown
            fi
            ;;
        *) OS_FAMILY=unknown; DISTRO=unknown ;;
    esac
    : "${DISTRO_VERSION:=unknown}"

    case "$(uname -m)" in
        x86_64|amd64) ARCH=amd64 ;;
        arm64|aarch64) ARCH=arm64 ;;
        *) ARCH="$(uname -m)" ;;
    esac
}

# report_platform prints what was found. It is always informational: a machine
# that is off the supported matrix should still get the full toolchain report,
# because that is what someone needs in order to decide what to do next.
report_platform() {
    head "This machine"
    printf '  os=%s distro=%s version=%s arch=%s\n' "$OS_FAMILY" "$DISTRO" "$DISTRO_VERSION" "$ARCH"

    case "$OS_FAMILY:$DISTRO:$DISTRO_VERSION:$ARCH" in
        darwin:macos:*:amd64|darwin:macos:*:arm64) return ;;
        linux:ubuntu:22.04:amd64|linux:ubuntu:22.04:arm64) return ;;
        linux:ubuntu:24.04:amd64|linux:ubuntu:24.04:arm64) return ;;
    esac
    warn "this platform is outside the supported matrix (macOS, Ubuntu 22.04, Ubuntu 24.04)"
    note "install.sh will refuse it. That is a supported-platform decision, not a toolchain problem."
}

# --- tool probes -----------------------------------------------------------

have() { command -v "$1" >/dev/null 2>&1; }

# version_at_least compares a dotted version against a minimum.
#
# Extraction is done with awk rather than shell parameter expansion. The
# expansion version of this function was written first and was wrong: inside
# ${x%%[0-9]*} the alternation is a trap, and it silently classified 1.26.2 as
# older than 1.26. A precheck that misjudges is worse than no precheck, because
# it sends someone to fix a machine that was already fine, so the parsing is now
# explicit and has its own unit test.
#
# Accepts "go1.26.2", "Python 3.11.9", "v24.14.1", "1.26.2-rc1". A missing minor
# or patch component counts as zero, so "1.26" is not older than "1.26.0".
# Anything with no leading digits at all is "unknown" and never passes.
version_at_least() {
    found="$1"
    want="$2"
    [ -n "$found" ] && [ -n "$want" ] || return 1

    have_version="$(printf '%s\n' "$found" | awk '
        { if (match($0, /[0-9]+(\.[0-9]+)*/)) {
            v = substr($0, RSTART, RLENGTH)
            n = split(v, a, ".")
            printf "%d %d %d", a[1], (n > 1 ? a[2] : 0), (n > 2 ? a[3] : 0)
          } }')"
    want_version="$(printf '%s\n' "$want" | awk '
        { if (match($0, /[0-9]+(\.[0-9]+)*/)) {
            v = substr($0, RSTART, RLENGTH)
            n = split(v, a, ".")
            printf "%d %d %d", a[1], (n > 1 ? a[2] : 0), (n > 2 ? a[3] : 0)
          } }')"
    [ -n "$have_version" ] && [ -n "$want_version" ] || return 1

    set -- $have_version
    f1="$1"; f2="$2"; f3="$3"
    set -- $want_version
    w1="$1"; w2="$2"; w3="$3"

    if [ "$f1" -ne "$w1" ]; then [ "$f1" -gt "$w1" ]; return $?; fi
    if [ "$f2" -ne "$w2" ]; then [ "$f2" -gt "$w2" ]; return $?; fi
    [ "$f3" -ge "$w3" ]
}

probe_go() {
    if ! have go; then
        fail "go is not installed (need 1.26+)"
        note "macOS: brew install go    Ubuntu: install from https://go.dev/dl/"
        return
    fi
    found="$(go version 2>/dev/null | awk '{print $3}')"
    if version_at_least "$found" "1.26"; then
        pass "go $found"
    else
        fail "go ${found:-unknown} is too old (need 1.26+)"
    fi
}

probe_python() {
    if ! have python3; then
        fail "python3 is not installed (need 3.11+; the project pins 3.11.9)"
        return
    fi
    found="$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"
    if version_at_least "$found" "3.11"; then
        pass "python3 $found"
    else
        fail "python3 ${found:-unknown} is too old (need 3.11+)"
    fi
}

probe_node() {
    if ! have node; then
        warn "node is not installed (only needed for the frontend tests, not to run the product)"
        return
    fi
    pass "node $(node --version 2>/dev/null)"
}

probe_docker() {
    if ! have docker; then
        warn "docker is not installed (only the cloud stack needs it)"
        return
    fi
    if docker info >/dev/null 2>&1; then
        pass "docker is installed and the daemon answers"
    else
        warn "docker is installed but the daemon is not reachable"
        note "start Docker, or run only the local and sim roles"
    fi
}

probe_openssl() {
    if have openssl; then
        pass "openssl $(openssl version 2>/dev/null | awk '{print $2}')"
    else
        fail "openssl is not installed (the robot and cloud roles generate and check certificates)"
    fi
}

# --- role checks -----------------------------------------------------------

# The platform rows mirror validate_role_platform in scripts/install/common.sh.
# They are repeated here rather than sourced because that file exits the shell
# on a mismatch, which is exactly what a precheck must not do.
check_role_platform() {
    role="$1"
    case "$role" in
        sim)
            case "$OS_FAMILY:$DISTRO:$DISTRO_VERSION" in
                darwin:macos:*|linux:ubuntu:22.04|linux:ubuntu:24.04) return 0 ;;
            esac ;;
        local)
            case "$OS_FAMILY:$DISTRO:$DISTRO_VERSION" in
                darwin:macos:*|linux:ubuntu:22.04|linux:ubuntu:24.04) return 0 ;;
            esac ;;
        robot-pi)
            case "$OS_FAMILY:$DISTRO:$DISTRO_VERSION:$ARCH" in
                linux:ubuntu:24.04:arm64) return 0 ;;
            esac ;;
        cloud)
            # The cloud role is not an install.sh role. The control plane is a
            # container stack driven by scripts/fleet-up.sh, so its requirement
            # is Docker, not a platform row.
            return 0 ;;
    esac
    return 1
}

check_sim() {
    head "Role: sim (simulation on this machine)"
    if check_role_platform sim; then
        pass "platform is supported for sim"
    else
        fail "platform is not supported for sim"
    fi
    probe_python
    probe_go
    probe_node
    if [ -d "$ROOT_DIR/artifacts/sim-assets" ]; then
        pass "simulation assets are present (artifacts/sim-assets)"
    else
        warn "artifacts/sim-assets is missing; the home scene will be prepared on first start"
        note "scripts/furnished-home-demo.sh regenerates it via scripts/prepare_home_world.py"
    fi
    if [ -d "$ROOT_DIR/artifacts/maps/furnished-home" ]; then
        pass "a furnished-home map is present (artifacts/maps/furnished-home)"
    else
        warn "no furnished-home map yet; run the mapping pass before a navigation task"
        note "see docs/guides/furnished-home-demo.md"
    fi
    if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
        pass "the project virtualenv exists (.venv)"
    else
        warn "no .venv yet; install.sh creates it"
    fi
}

check_local() {
    head "Role: local (Local Agent and console on this machine)"
    if check_role_platform local; then
        pass "platform is supported for local"
    else
        fail "platform is not supported for local"
    fi
    probe_go
    probe_python
    if [ -w "$ROOT_DIR" ]; then
        pass "the checkout is writable (the build writes bin/ and .venv/)"
    else
        fail "the checkout is not writable: $ROOT_DIR"
    fi
}

check_robot_pi() {
    head "Role: robot-pi (thin Robot Runtime on the robot)"
    if check_role_platform robot-pi; then
        pass "platform is supported for robot-pi"
    else
        fail "platform is not supported for robot-pi (needs Ubuntu 24.04 arm64)"
        note "detected os=$OS_FAMILY distro=$DISTRO version=$DISTRO_VERSION arch=$ARCH"
    fi
    probe_python
    probe_openssl
    if [ -r /etc/tangying-robot-agent-os/robot-pi.env ]; then
        pass "robot-pi.env is present"
    else
        warn "robot-pi.env is not installed yet"
        note "template: deploy/robot/raspberry-pi/robot-pi.env.example"
    fi
    # The serial devices and calibration are what scripts/robot-pi-preflight.sh
    # checks; this only reports whether that check can run at all.
    if [ -x "$ROOT_DIR/scripts/robot-pi-preflight.sh" ]; then
        pass "the motion-free preflight is available (scripts/robot-pi-preflight.sh)"
    fi
    if [ -e /dev/tangying-left ] || [ -e /dev/tangying-right ]; then
        pass "the stable serial device symlinks exist"
    else
        warn "no /dev/tangying-* symlinks; run only after installing the udev rule"
        note "deploy/robot/raspberry-pi/99-tangying-xlerobot.rules"
    fi
}

check_cloud() {
    head "Role: cloud (Fleet control plane as a container stack)"
    probe_docker
    probe_openssl
    if [ -f "$ROOT_DIR/deploy/cloud/docker-compose.yml" ]; then
        pass "the cloud compose file is present"
    else
        fail "deploy/cloud/docker-compose.yml is missing; the checkout looks incomplete"
    fi
    if [ -f "$ROOT_DIR/deploy/cloud/.env" ]; then
        pass "deploy/cloud/.env exists (secrets already generated)"
    else
        warn "no deploy/cloud/.env yet; fleet-up.sh generates it on first up"
    fi
    # Ports are only a warning: this runs before the stack exists, and a port in
    # use by something else is a fact worth knowing, not a blocker for reading.
    if have lsof; then
        for port in "${FLEET_HTTPS_PORT:-443}" "${FLEET_GRPC_PORT:-8444}"; do
            if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
                warn "port $port is already in use; the cloud stack publishes on it"
                note "on macOS this is often Docker Desktop itself, which is where the stack will run"
            fi
        done
    else
        warn "lsof is unavailable, so port conflicts could not be checked"
    fi
}

# --- main ------------------------------------------------------------------

ROLES=()
for argument in "$@"; do
    case "$argument" in
        -h|--help) usage; exit 0 ;;
        sim|local|robot-pi|cloud) ROLES+=("$argument") ;;
        *) printf 'precheck: unknown role %s\n\n' "$argument" >&2; usage >&2; exit 2 ;;
    esac
done
if [ "${#ROLES[@]}" -eq 0 ]; then
    ROLES=(sim local robot-pi cloud)
fi

detect_platform
printf '%sTangying Robot AgentOS cold-start precheck%s\n' "$C_HEAD" "$C_OFF"
report_platform

for role in "${ROLES[@]}"; do
    "check_${role//-/_}"
done

head "Summary"
if [ "$FAILURES" -eq 0 ]; then
    printf '  %spassed%s: %d check(s) passed, %d warning(s)\n' "$C_PASS" "$C_OFF" "$PASSES" "$WARNINGS"
    printf '  Nothing here blocks an install. Next: ./install.sh <role>\n'
    exit 0
fi
# The failed count is named explicitly rather than left as "N failed": on a
# multi-role run the reader needs to know these are required checks, and that the
# warnings above are a different, non-blocking category.
printf '  %sblocked%s: %d required check(s) failed, %d passed, %d warning(s)\n' \
    "$C_FAIL" "$C_OFF" "$FAILURES" "$PASSES" "$WARNINGS"
printf '  Fix the failures above, then run this again. Warnings do not block.\n'
exit 1
