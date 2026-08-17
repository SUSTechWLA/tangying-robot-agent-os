#!/usr/bin/env bash

install_role() {
  info "preparing simulation development stack"
  if [ "$ROBOT_AGENT_OS" = "linux" ]; then
    ROBOT_AGENT_CONFIG_DIR=${ROBOT_AGENT_CONFIG_DIR:-$HOME/.config/tangying-robot-agent-os}
    ROBOT_AGENT_STATE_DIR=${ROBOT_AGENT_STATE_DIR:-$HOME/.local/share/tangying-robot-agent-os}
    export ROBOT_AGENT_CONFIG_DIR ROBOT_AGENT_STATE_DIR
  fi
  confirm_mutation
  if [ "$ROBOT_AGENT_OS" = "darwin" ]; then
    ensure_homebrew
    run brew install go python@3.11 protobuf
  else
    sudo_run apt-get update
    if [ "$ROBOT_AGENT_OS_VERSION" = "22.04" ]; then
      sudo_run apt-get install -y software-properties-common
      sudo_run add-apt-repository -y ppa:deadsnakes/ppa
      sudo_run apt-get update
      sudo_run apt-get install -y python3.11 python3.11-venv python3.11-dev protobuf-compiler curl ca-certificates build-essential
      ROBOT_AGENT_PYTHON=python3.11
      export ROBOT_AGENT_PYTHON
    else
      sudo_run apt-get install -y python3 python3-venv python3-dev protobuf-compiler curl ca-certificates build-essential
    fi
    ensure_go
  fi
  install_python_project
  build_go_binaries
  write_receipt
  info "simulation installed; run: ./bin/robot-agent demo"
}
