#!/usr/bin/env bash

: "${ROBOT_AGENT_ROOT:?ROBOT_AGENT_ROOT is required}"
: "${ROBOT_AGENT_ROLE:?ROBOT_AGENT_ROLE is required}"
: "${ROBOT_AGENT_DRY_RUN:=0}"
: "${ROBOT_AGENT_ASSUME_YES:=0}"

ROBOT_AGENT_GO_VERSION=${ROBOT_AGENT_GO_VERSION:-1.26.2}
ROBOT_AGENT_XLEROBOT_COMMIT=${ROBOT_AGENT_XLEROBOT_COMMIT:-3d14695e40c9c68229c0aacffca6053c75cd3eb6}

die() {
  echo "error: $*" >&2
  exit 1
}

info() {
  echo "==> $*"
}

shell_quote() {
  printf '%q' "$1"
}

run() {
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    printf 'DRY-RUN'
    for argument in "$@"; do
      printf ' '
      shell_quote "$argument"
    done
    printf '\n'
    return 0
  fi
  "$@"
}

run_in_root() {
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    printf 'DRY-RUN cd '
    shell_quote "$ROBOT_AGENT_ROOT"
    printf ' &&'
    for argument in "$@"; do
      printf ' '
      shell_quote "$argument"
    done
    printf '\n'
    return 0
  fi
  (cd "$ROBOT_AGENT_ROOT" && "$@")
}

sudo_run() {
  if [ "$(id -u)" -eq 0 ]; then
    run "$@"
  else
    run sudo "$@"
  fi
}

confirm_mutation() {
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ] || [ "$ROBOT_AGENT_ASSUME_YES" = "1" ]; then
    return 0
  fi
  if [ ! -t 0 ]; then
    die "package installation requires --yes in a noninteractive shell"
  fi
  printf 'Install system dependencies for role %s? [y/N] ' "$ROBOT_AGENT_ROLE" >&2
  read -r answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) die "installation cancelled" ;;
  esac
}

normalize_arch() {
  case "$1" in
    x86_64|amd64) echo amd64 ;;
    arm64|aarch64) echo arm64 ;;
    *) echo "$1" ;;
  esac
}

detect_platform() {
  if [ "${ROBOT_AGENT_TEST_MODE:-0}" = "1" ]; then
    ROBOT_AGENT_OS=${ROBOT_AGENT_TEST_OS:-linux}
    ROBOT_AGENT_DISTRO=${ROBOT_AGENT_TEST_DISTRO:-ubuntu}
    ROBOT_AGENT_OS_VERSION=${ROBOT_AGENT_TEST_VERSION:-24.04}
    ROBOT_AGENT_ARCH=$(normalize_arch "${ROBOT_AGENT_TEST_ARCH:-amd64}")
    export ROBOT_AGENT_OS ROBOT_AGENT_DISTRO ROBOT_AGENT_OS_VERSION ROBOT_AGENT_ARCH
    return
  fi

  case "$(uname -s)" in
    Darwin)
      ROBOT_AGENT_OS=darwin
      ROBOT_AGENT_DISTRO=macos
      ROBOT_AGENT_OS_VERSION=$(sw_vers -productVersion | cut -d. -f1)
      ;;
    Linux)
      ROBOT_AGENT_OS=linux
      if [ ! -r /etc/os-release ]; then
        die "unsupported platform: Linux without /etc/os-release"
      fi
      # shellcheck disable=SC1091
      . /etc/os-release
      ROBOT_AGENT_DISTRO=${ID:-unknown}
      ROBOT_AGENT_OS_VERSION=${VERSION_ID:-unknown}
      ;;
    *)
      ROBOT_AGENT_OS=unsupported
      ROBOT_AGENT_DISTRO=unknown
      ROBOT_AGENT_OS_VERSION=unknown
      ;;
  esac
  ROBOT_AGENT_ARCH=$(normalize_arch "$(uname -m)")
  export ROBOT_AGENT_OS ROBOT_AGENT_DISTRO ROBOT_AGENT_OS_VERSION ROBOT_AGENT_ARCH
}

validate_role_platform() {
  role=$1
  case "$role:$ROBOT_AGENT_OS:$ROBOT_AGENT_DISTRO:$ROBOT_AGENT_OS_VERSION:$ROBOT_AGENT_ARCH" in
    sim:darwin:macos:*:amd64|sim:darwin:macos:*:arm64) return ;;
    sim:linux:ubuntu:22.04:amd64|sim:linux:ubuntu:22.04:arm64) return ;;
    sim:linux:ubuntu:24.04:amd64|sim:linux:ubuntu:24.04:arm64) return ;;
    local:darwin:macos:*:amd64|local:darwin:macos:*:arm64) return ;;
    local:linux:ubuntu:22.04:amd64|local:linux:ubuntu:22.04:arm64) return ;;
    local:linux:ubuntu:24.04:amd64|local:linux:ubuntu:24.04:arm64) return ;;
    cloud:linux:ubuntu:22.04:amd64|cloud:linux:ubuntu:22.04:arm64) return ;;
    cloud:linux:ubuntu:24.04:amd64|cloud:linux:ubuntu:24.04:arm64) return ;;
    cloud:linux:debian:12:amd64|cloud:linux:debian:12:arm64) return ;;
    robot-pi:linux:ubuntu:24.04:arm64) return ;;
  esac
  die "unsupported platform for $role: os=$ROBOT_AGENT_OS distro=$ROBOT_AGENT_DISTRO version=$ROBOT_AGENT_OS_VERSION arch=$ROBOT_AGENT_ARCH"
}

resolved_version() {
  if [ -n "${ROBOT_AGENT_VERSION:-}" ]; then
    echo "$ROBOT_AGENT_VERSION"
  elif command -v git >/dev/null 2>&1 && git -C "$ROBOT_AGENT_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "$ROBOT_AGENT_ROOT" describe --tags --always --dirty
  else
    echo unknown
  fi
}

print_plan_header() {
  echo "PLAN role=$1 os=$ROBOT_AGENT_OS distro=$ROBOT_AGENT_DISTRO version=$ROBOT_AGENT_OS_VERSION arch=$ROBOT_AGENT_ARCH release=$(resolved_version)"
}

state_dir() {
  if [ -n "${ROBOT_AGENT_STATE_DIR:-}" ]; then
    echo "$ROBOT_AGENT_STATE_DIR"
  elif [ "$ROBOT_AGENT_OS" = "darwin" ]; then
    echo "$HOME/Library/Application Support/TangyingRobotAgent"
  else
    echo /var/lib/tangying-robot-agent-os
  fi
}

config_dir() {
  if [ -n "${ROBOT_AGENT_CONFIG_DIR:-}" ]; then
    echo "$ROBOT_AGENT_CONFIG_DIR"
  elif [ "$ROBOT_AGENT_OS" = "darwin" ]; then
    echo "$(state_dir)"
  else
    echo /etc/tangying-robot-agent-os
  fi
}

install_dir() {
  if [ -n "${ROBOT_AGENT_INSTALL_DIR:-}" ]; then
    echo "$ROBOT_AGENT_INSTALL_DIR"
  elif [ "$ROBOT_AGENT_OS" = "darwin" ]; then
    echo /Users/Shared/TangyingRobotAgent
  else
    echo /opt/tangying-robot-agent-os
  fi
}

ensure_directory() {
  directory=$1
  mode=${2:-0755}
  if directory_is_user_managed "$directory"; then
    run mkdir -p "$directory"
    if [ "$ROBOT_AGENT_DRY_RUN" != "1" ]; then chmod "$mode" "$directory"; fi
  else
    sudo_run install -d -m "$mode" "$directory"
  fi
}

directory_is_user_managed() {
  directory=$1
  if [ "$ROBOT_AGENT_OS" = "darwin" ]; then return 0; fi
  case "$directory" in
    "$HOME"|"$HOME"/*) return 0 ;;
  esac
  if [ -n "${ROBOT_AGENT_STATE_DIR:-}" ]; then
    case "$directory" in
      "$ROBOT_AGENT_STATE_DIR"|"$ROBOT_AGENT_STATE_DIR"/*) return 0 ;;
    esac
  fi
  if [ -n "${ROBOT_AGENT_CONFIG_DIR:-}" ]; then
    case "$directory" in
      "$ROBOT_AGENT_CONFIG_DIR"|"$ROBOT_AGENT_CONFIG_DIR"/*) return 0 ;;
    esac
  fi
  return 1
}

install_config_example() {
  example=$1
  destination=$2
  ensure_directory "$(dirname "$destination")" 0700
  if [ -e "$destination" ]; then
    info "preserving existing config: $destination"
    return
  fi
  if [ "$ROBOT_AGENT_OS" = "darwin" ] || [ -n "${ROBOT_AGENT_CONFIG_DIR:-}" ]; then
    run install -m 0600 "$example" "$destination"
  else
    sudo_run install -m 0600 "$example" "$destination"
  fi
}

write_receipt() {
  destination="$(state_dir)/install.json"
  ensure_directory "$(dirname "$destination")" 0700
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN write receipt $destination"
    return
  fi
  commit=$(git -C "$ROBOT_AGENT_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)
  timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  temporary=$(mktemp "${TMPDIR:-/tmp}/tangying-install-receipt.XXXXXX")
  umask 077
  printf '{\n  "role": "%s",\n  "version": "%s",\n  "commit": "%s",\n  "os": "%s",\n  "distro": "%s",\n  "osVersion": "%s",\n  "arch": "%s",\n  "installedAt": "%s"\n}\n' \
    "$ROBOT_AGENT_ROLE" "$(resolved_version)" "$commit" "$ROBOT_AGENT_OS" \
    "$ROBOT_AGENT_DISTRO" "$ROBOT_AGENT_OS_VERSION" "$ROBOT_AGENT_ARCH" "$timestamp" >"$temporary"
  chmod 0600 "$temporary"
  if [ "$ROBOT_AGENT_OS" = "darwin" ] || [ -n "${ROBOT_AGENT_STATE_DIR:-}" ]; then
    mv "$temporary" "$destination"
  else
    sudo_run install -m 0600 "$temporary" "$destination"
    rm -f "$temporary"
  fi
}

ensure_homebrew() {
  if command -v brew >/dev/null 2>&1; then return; fi
  confirm_mutation
  installer=$(mktemp "${TMPDIR:-/tmp}/homebrew-install.XXXXXX")
  run curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$installer"
  run /bin/bash "$installer"
  if [ "$ROBOT_AGENT_DRY_RUN" != "1" ]; then rm -f "$installer"; fi
}

install_go_linux() {
  if command -v go >/dev/null 2>&1 && go version | grep -q "go${ROBOT_AGENT_GO_VERSION}"; then return; fi
  confirm_mutation
  archive="go${ROBOT_AGENT_GO_VERSION}.linux-${ROBOT_AGENT_ARCH}.tar.gz"
  case "$ROBOT_AGENT_ARCH" in
    amd64) checksum=990e6b4bbba816dc3ee129eaeaf4b42f17c2800b88a2166c265ac1a200262282 ;;
    arm64) checksum=c958a1fe1b361391db163a485e21f5f228142d6f8b584f6bef89b26f66dc5b23 ;;
    *) die "no Go toolchain checksum for $ROBOT_AGENT_ARCH" ;;
  esac
  temporary="${TMPDIR:-/tmp}/$archive"
  target="/usr/local/lib/tangying-go/${ROBOT_AGENT_GO_VERSION}"
  run curl -fsSL "https://go.dev/dl/$archive" -o "$temporary"
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN verify sha256=$checksum file=$temporary"
  else
    actual=$(shasum -a 256 "$temporary" | awk '{print $1}')
    [ "$actual" = "$checksum" ] || die "Go archive checksum mismatch"
  fi
  sudo_run install -d -m 0755 "$target"
  sudo_run tar -xzf "$temporary" --strip-components=1 -C "$target"
  sudo_run ln -sfn "$target/bin/go" /usr/local/bin/go
  sudo_run ln -sfn "$target/bin/gofmt" /usr/local/bin/gofmt
  if [ "$ROBOT_AGENT_DRY_RUN" != "1" ]; then rm -f "$temporary"; fi
}

ensure_go() {
  if [ "$ROBOT_AGENT_OS" = "darwin" ]; then
    ensure_homebrew
    run brew install go
  else
    install_go_linux
  fi
}

select_python() {
  for candidate in "${ROBOT_AGENT_PYTHON:-}" python3.12 python3.11 python3; do
    [ -n "$candidate" ] || continue
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
      echo "$candidate"
      return
    fi
  done
  die "Python 3.11 or newer is required"
}

install_python_project() {
  python=$(select_python)
  run_in_root "$python" -m venv .venv
  run_in_root .venv/bin/python -m pip install --upgrade pip
  run_in_root .venv/bin/pip install -e '.[dev]'
}

build_go_binaries() {
  build_version=$(resolved_version)
  run_in_root mkdir -p bin
  run_in_root go build -ldflags "-X main.version=$build_version" -o bin/robot-agent ./cmd/robot-agent
  run_in_root go build -o bin/local-agent ./cmd/local-agent
  run_in_root go build -o bin/cloud-control-plane ./cmd/cloud-control-plane
}

install_repository_checkout() {
  destination=$1
  info "install repository checkout into $destination"
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN install repository checkout source=$ROBOT_AGENT_ROOT destination=$destination"
    return
  fi
  archive=$(mktemp "${TMPDIR:-/tmp}/tangying-robot-agent-os.XXXXXX.tar")
  git -C "$ROBOT_AGENT_ROOT" archive --format=tar -o "$archive" HEAD
  if [ "$ROBOT_AGENT_OS" = "darwin" ]; then
    install -d -m 0755 "$destination"
    tar -xf "$archive" -C "$destination"
  else
    sudo install -d -m 0755 "$destination"
    sudo tar -xf "$archive" -C "$destination"
  fi
  rm -f "$archive"
}

install_robot_agent_cli() {
  info "install robot-agent CLI"
  build_version=$(resolved_version)
  run_in_root mkdir -p bin
  run_in_root go build -ldflags "-X main.version=$build_version" -o bin/robot-agent ./cmd/robot-agent
  sudo_run install -m 0755 "$ROBOT_AGENT_ROOT/bin/robot-agent" /usr/local/bin/robot-agent
}
