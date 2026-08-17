#!/usr/bin/env bash

install_ros_jazzy() {
  confirm_mutation
  sudo_run apt-get update
  sudo_run apt-get install -y software-properties-common curl ca-certificates gnupg locales git python3-venv
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

install_robot_services() {
  sudo_run install -m 0644 "$ROBOT_AGENT_ROOT/deploy/raspberry-pi/tangying-xlerobot.service" /etc/systemd/system/tangying-xlerobot.service
  sudo_run install -m 0644 "$ROBOT_AGENT_ROOT/deploy/raspberry-pi/tangying-robot-edge.service" /etc/systemd/system/tangying-robot-edge.service
  sudo_run install -m 0644 "$ROBOT_AGENT_ROOT/deploy/raspberry-pi/99-tangying-xlerobot.rules" /etc/udev/rules.d/99-tangying-xlerobot.rules
  sudo_run systemctl daemon-reload
  sudo_run udevadm control --reload-rules
}

install_role() {
  info "preparing Raspberry Pi Robot Edge"
  install_ros_jazzy
  install_xlerobot_source
  install_config_example "$ROBOT_AGENT_ROOT/deploy/config/robot-pi.env.example" "$(config_dir)/robot-pi.env"
  ensure_directory "$(state_dir)/certs" 0700
  ensure_directory "$(state_dir)/calibration" 0750
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN rosdep install workspace dependencies"
    echo "DRY-RUN colcon build 4 ROS packages"
  else
    # shellcheck disable=SC1091
    . /opt/ros/jazzy/setup.sh
    rosdep install --from-paths "$ROBOT_AGENT_ROOT/robot/ros2_ws/src" --ignore-src --skip-keys ament_python -r -y
    (cd "$ROBOT_AGENT_ROOT/robot/ros2_ws" && colcon build --event-handlers console_cohesion+)
  fi
  install_robot_services
  write_receipt
  info "Robot Edge installed but stopped pending certificates, serial devices, calibration, and safety checklist"
}

