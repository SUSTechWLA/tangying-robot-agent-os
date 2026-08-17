#!/usr/bin/env bash

install_local_service() {
  destination=$(install_dir)
  ensure_directory "$destination/bin" 0755
  if [ "$ROBOT_AGENT_OS" = "darwin" ]; then
    run install -m 0755 "$ROBOT_AGENT_ROOT/bin/robot-agent" "$destination/bin/robot-agent"
    run install -m 0755 "$ROBOT_AGENT_ROOT/bin/local-agent" "$destination/bin/local-agent"
    ensure_directory "$HOME/Library/LaunchAgents" 0755
    ensure_directory "$(state_dir)/logs" 0700
    if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
      echo "DRY-RUN render launchd plist with home=$HOME"
    else
      rendered="$(state_dir)/com.tangying.robot-agent.plist"
      escaped_home=$(printf '%s' "$HOME" | sed 's/[&|]/\\&/g')
      sed "s|__HOME__|$escaped_home|g" "$ROBOT_AGENT_ROOT/deploy/laptop/com.tangying.robot-agent.plist" >"$rendered"
      install -m 0644 "$rendered" "$HOME/Library/LaunchAgents/com.tangying.robot-agent.plist"
    fi
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
  if [ "$ROBOT_AGENT_OS" = "linux" ]; then
    ROBOT_AGENT_CONFIG_DIR=${ROBOT_AGENT_CONFIG_DIR:-$HOME/.config/tangying-robot-agent-os}
    ROBOT_AGENT_STATE_DIR=${ROBOT_AGENT_STATE_DIR:-$HOME/.local/share/tangying-robot-agent-os}
    export ROBOT_AGENT_CONFIG_DIR ROBOT_AGENT_STATE_DIR
  fi
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
  destination=$(install_dir)
  install_repository_checkout "$destination"
  install_robot_agent_cli
  install_config_example "$destination/deploy/config/local.env.example" "$(config_dir)/local.env"
  ensure_directory "$(state_dir)/certs" 0700
  install_local_service
  write_receipt
  info "Local Agent installed but not started; run robot-agent configure, then robot-agent start local"
}
