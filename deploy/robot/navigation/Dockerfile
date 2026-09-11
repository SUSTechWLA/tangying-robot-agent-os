# Official OSRF multiarch Jazzy image. CI/deployments may pin ROS_IMAGE to a reviewed digest.
ARG ROS_IMAGE=ros:jazzy-ros-base
FROM ${ROS_IMAGE}
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-jazzy-rtabmap-ros ros-jazzy-navigation2 ros-jazzy-nav2-bringup \
    ros-jazzy-ros-gz-sim ros-jazzy-ros-gz-bridge \
    ros-jazzy-tf2-geometry-msgs ros-jazzy-sensor-msgs-py \
    python3-colcon-common-extensions python3-venv python3-numpy python3-pytest \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends ros-jazzy-rmw-cyclonedds-cpp \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/navigation-venv \
    && /opt/navigation-venv/bin/pip install --no-cache-dir 'grpcio==1.83.0' 'protobuf==6.33.5'
COPY python/tangying_robot_proto /opt/tangying-proto/tangying_robot_proto
COPY robot/ros2_ws/src/tangying_navigation /opt/tangying-nav/src/tangying_navigation
COPY robot/ros2_ws/src/tangying_dwb_critics /opt/tangying-nav/src/tangying_dwb_critics
WORKDIR /opt/tangying-nav
ENV PYTHONPATH=/opt/tangying-proto:/opt/navigation-venv/lib/python3.12/site-packages
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
RUN source /opt/ros/jazzy/setup.bash && colcon build --packages-up-to tangying_navigation \
    && source install/setup.bash && python3 -c 'from tangying_robot_proto.robot.v1 import robot_pb2_grpc'
COPY deploy/robot/navigation/entrypoint.sh /usr/local/bin/tangying-navigation
RUN chmod 755 /usr/local/bin/tangying-navigation && mkdir -p /data/maps
ENTRYPOINT ["/usr/local/bin/tangying-navigation"]
STOPSIGNAL SIGINT
