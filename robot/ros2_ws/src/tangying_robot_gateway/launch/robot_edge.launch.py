from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription(
        [
            Node(
                package="tangying_safety_supervisor",
                executable="supervisor",
                name="tangying_safety_supervisor",
                output="screen",
            ),
            Node(
                package="tangying_ros_gateway",
                executable="gateway",
                name="tangying_ros_gateway",
                output="screen",
            ),
        ]
    )

