#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/jazzy/setup.bash
source /tmp/tangying-gazebo-field-20261028-001/ros-install/setup.bash
for key in ${!AGENT_@} ${!EDGE_@} ${!TANGYING_@}; do unset "$key"; done
export ROS_DOMAIN_ID=128
export ROS_LOCALHOST_ONLY=1
export GZ_PARTITION=tangying-field-20261028-001
export GZ_SIM_SYSTEM_PLUGIN_PATH=/tmp/tangying-gazebo-field-20261028-001/build${GZ_SIM_SYSTEM_PLUGIN_PATH:+:$GZ_SIM_SYSTEM_PLUGIN_PATH}
export QT_QPA_PLATFORM=offscreen
export PYTHONPATH=/tmp/tangying-gazebo-field-20261028-001/source/robot/gateway:/tmp/tangying-gazebo-field-20261028-001/source/robot/ros2_ws/src/tangying_robot_gateway:/tmp/tangying-gazebo-field-20261028-001/source/python:/tmp/tangying-gazebo-field-20261028-001/source${PYTHONPATH:+:$PYTHONPATH}
cd /tmp/tangying-gazebo-field-20261028-001/source
exec "$@"
