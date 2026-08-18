#!/usr/bin/env bash

install_ros_jazzy() {
  confirm_mutation
  sudo_run apt-get update
  sudo_run apt-get install -y software-properties-common curl ca-certificates gnupg locales git openssl python3-venv
  sudo_run locale-gen en_US en_US.UTF-8
  sudo_run update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
  sudo_run add-apt-repository -y universe
  run curl -fsSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /tmp/tangying-ros.key
  sudo_run install -m 0644 /tmp/tangying-ros.key /usr/share/keyrings/ros-archive-keyring.gpg
  repository="deb [arch=$ROBOT_AGENT_ARCH signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu noble main"
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN write /etc/apt/sources.list.d/ros2.list: $repository"
  else
    echo "$repository" | sudo tee /etc/apt/sources.list.d/ros2.list >/dev/null
  fi
  sudo_run apt-get update
  sudo_run apt-get install -y ros-jazzy-ros-base python3-colcon-common-extensions python3-rosdep
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN rosdep update"
  else
    rosdep update
  fi
}

ensure_robot_user() {
  if id tangying-robot >/dev/null 2>&1; then return; fi
  sudo_run useradd --system --create-home --groups dialout --shell /bin/bash tangying-robot
}

install_xlerobot_source() {
  destination=/opt/XLeRobot
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN clone XLeRobot commit=$ROBOT_AGENT_XLEROBOT_COMMIT destination=$destination"
    return
  fi
  if [ ! -d "$destination/.git" ]; then
    sudo git clone https://github.com/Vector-Wangel/XLeRobot.git "$destination"
  fi
  sudo git -C "$destination" fetch origin "$ROBOT_AGENT_XLEROBOT_COMMIT"
  sudo git -C "$destination" checkout --detach "$ROBOT_AGENT_XLEROBOT_COMMIT"
}

install_edge_python() {
  destination=/opt/tangying-robot-agent-os
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN create system-site-packages venv and install gateway plus LeRobot"
    echo "DRY-RUN copy pinned XLeRobot two-wheel integration into lerobot.robots"
    return
  fi
  sudo python3 -m venv --system-site-packages "$destination/.venv"
  sudo "$destination/.venv/bin/pip" install --upgrade pip
  sudo "$destination/.venv/bin/pip" install -e "$destination" 'lerobot==0.4.1'
  lerobot_robots=$(sudo "$destination/.venv/bin/python" -c 'import pathlib,lerobot.robots; print(pathlib.Path(lerobot.robots.__file__).parent)')
  lerobot_root=$(sudo "$destination/.venv/bin/python" -c 'import pathlib,lerobot; print(pathlib.Path(lerobot.__file__).parent)')
  sudo cp -R /opt/XLeRobot/software/src/robots/xlerobot_2wheels "$lerobot_robots/"
  sudo cp -R /opt/XLeRobot/software/src/model "$lerobot_root/"
}

install_robot_services() {
  if direct_edge; then
    sudo_run install -m 0644 "$ROBOT_AGENT_ROOT/deploy/raspberry-pi/tangying-robot-edge-direct.service" /etc/systemd/system/tangying-robot-edge.service
  else
    sudo_run install -m 0644 "$ROBOT_AGENT_ROOT/deploy/raspberry-pi/tangying-xlerobot.service" /etc/systemd/system/tangying-xlerobot.service
    sudo_run install -m 0644 "$ROBOT_AGENT_ROOT/deploy/raspberry-pi/tangying-robot-edge.service" /etc/systemd/system/tangying-robot-edge.service
  fi
  sudo_run install -m 0644 "$ROBOT_AGENT_ROOT/deploy/raspberry-pi/99-tangying-xlerobot.rules" /etc/udev/rules.d/99-tangying-xlerobot.rules
  sudo_run systemctl daemon-reload
  sudo_run udevadm control --reload-rules
}

direct_edge() {
  [ "${ROBOT_AGENT_DIRECT_EDGE:-0}" = "1" ]
}

install_role() {
  info "preparing Raspberry Pi Robot Edge"
  if direct_edge; then
    info "using ROS2-free direct XLeRobot backend"
  else
    install_ros_jazzy
  fi
  ensure_go
  ensure_robot_user
  install_xlerobot_source
  install_repository_checkout /opt/tangying-robot-agent-os
  install_robot_agent_cli
  if ! direct_edge && [ "$ROBOT_AGENT_DRY_RUN" != "1" ]; then
    sudo chown -R tangying-robot:tangying-robot /opt/tangying-robot-agent-os/robot/ros2_ws
  fi
  install_edge_python
  install_config_example "$ROBOT_AGENT_ROOT/deploy/config/robot-pi.env.example" "$(config_dir)/robot-pi.env"
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN chown robot configuration to tangying-robot"
  else
    sudo chown tangying-robot:tangying-robot "$(config_dir)/robot-pi.env"
  fi
  ensure_directory "$(state_dir)/certs" 0700
  ensure_directory "$(state_dir)/calibration" 0750
  if direct_edge; then
    if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
      echo "DRY-RUN skip ROS2 workspace build for direct edge"
    fi
  elif [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN rosdep install workspace dependencies"
    echo "DRY-RUN colcon build 4 ROS packages"
  else
    # shellcheck disable=SC1091
    . /opt/ros/jazzy/setup.sh
    rosdep install --from-paths /opt/tangying-robot-agent-os/robot/ros2_ws/src --ignore-src --skip-keys ament_python -r -y
    sudo -u tangying-robot /bin/bash -lc 'source /opt/ros/jazzy/setup.bash && cd /opt/tangying-robot-agent-os/robot/ros2_ws && colcon build --event-handlers console_cohesion+'
  fi
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN chown robot state to tangying-robot"
  else
    sudo chown -R tangying-robot:tangying-robot "$(state_dir)"
  fi
  install_robot_services
  write_receipt
  info "Robot Edge installed but stopped pending certificates, serial devices, calibration, and safety checklist"
}
