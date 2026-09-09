#!/usr/bin/env bash
set -eo pipefail
# Official ROS setup scripts read optional environment variables directly.
# Restore nounset after their initialization, before processing our configuration.
source /opt/ros/jazzy/setup.bash
source /opt/tangying-nav/install/setup.bash
set -u
case "${TANGYING_NAVIGATION_MODE:-mapping}" in
  mapping|localization) ;;
  *) echo 'navigation mode must be mapping or localization' >&2; exit 2 ;;
esac
case "${TANGYING_NAVIGATION_SCENE:-tabletop}" in
  tabletop|home|gazebo_house) ;;
  *) echo 'navigation scene must be tabletop, home or gazebo_house' >&2; exit 2 ;;
esac
if [[ -z "${TANGYING_NAVIGATION_TOKEN:-}" ]]; then
  echo 'TANGYING_NAVIGATION_TOKEN is required' >&2; exit 2
fi
if [[ "${TANGYING_NAVIGATION_SCENE:-tabletop}" == "gazebo_house" ]]; then
  exec ros2 launch tangying_navigation gazebo_house.launch.py \
    mode:="${TANGYING_NAVIGATION_MODE:-mapping}" \
    database_path:="${TANGYING_NAVIGATION_DATABASE:-/data/maps/gazebo_house/rtabmap.db}"
fi
exec ros2 launch tangying_navigation navigation.launch.py \
  mode:="${TANGYING_NAVIGATION_MODE:-mapping}" \
  scene:="${TANGYING_NAVIGATION_SCENE:-tabletop}" \
  input_mode:="${TANGYING_NAVIGATION_INPUT_MODE:-runtime}" \
  database_path:="${TANGYING_NAVIGATION_DATABASE:-/data/maps/rtabmap.db}"
