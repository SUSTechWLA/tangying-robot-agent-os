#!/usr/bin/env bash

install_local_service() {
  destination=$(install_dir)
  ensure_directory "$destination/bin" 0755
  if [ "$ROBOT_AGENT_OS" = "darwin" ]; then
    run install -m 0755 "$ROBOT_AGENT_ROOT/bin/robot-agent" "$destination/bin/robot-agent"
    run install -m 0755 "$ROBOT_AGENT_ROOT/bin/local-agent" "$destination/bin/local-agent"
    ensure_directory "$HOME/Library/LaunchAgents" 0755
    run install -m 0644 "$ROBOT_AGENT_ROOT/deploy/laptop/com.tangying.robot-agent.plist" "$HOME/Library/LaunchAgents/com.tangying.robot-agent.plist"
  else
    sudo_run install -m 0755 "$ROBOT_AGENT_ROOT/bin/robot-agent" /usr/local/bin/robot-agent
    sudo_run install -m 0755 "$ROBOT_AGENT_ROOT/bin/local-agent" /usr/local/bin/tangying-local-agent
    ensure_directory "$HOME/.config/systemd/user" 0755
    run install -m 0644 "$ROBOT_AGENT_ROOT/deploy/laptop/tangying-robot-local-agent.service" "$HOME/.config/systemd/user/tangying-robot-local-agent.service"
    run systemctl --user daemon-reload
  fi
}

install_role() {
  info "preparing laptop Local Agent"
  confirm_mutation
  if [ "$ROBOT_AGENT_OS" = "darwin" ]; then
    ensure_homebrew
    run brew install go
  else
    sudo_run apt-get update
    sudo_run apt-get install -y ca-certificates curl git openssl
    ensure_go
  fi
  build_go_binaries
  install_config_example "$ROBOT_AGENT_ROOT/deploy/config/local.env.example" "$(config_dir)/local.env"
  ensure_directory "$(state_dir)/certs" 0700
  install_local_service
  write_receipt
  info "Local Agent installed but not started; run robot-agent configure, then robot-agent start local"
}

