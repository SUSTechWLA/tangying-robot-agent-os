"""Run the Gazebo Harmonic household world against the normal ROS2 boundary.

This launch file deliberately includes the same navigation launch used by a
physical robot.  Only the sensor/actuator source changes: Gazebo publishes the
RGB-D, odometry and command topics through ros_gz_bridge, while RTAB-Map and
Nav2 remain the production path.
"""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    SetEnvironmentVariable,
    SetLaunchConfiguration,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ros_gz_sim.actions import GzServer


def generate_launch_description():
    share = Path(get_package_share_directory("tangying_navigation"))
    world = share / "worlds/tangying_home.sdf"
    bridge_config = share / "config/gazebo_house_bridge.yaml"
    mode = DeclareLaunchArgument("mode", default_value="mapping", choices=["mapping", "localization"])
    # Declare the boundary arguments here as well as in navigation.launch.py.
    # This prevents the included launch file's runtime defaults from winning
    # before its OpaqueFunction evaluates (which would accidentally start the
    # authenticated runtime RGB-D bridge instead of the Gazebo ROS bridge).
    scene = DeclareLaunchArgument("scene", default_value="gazebo_house", choices=["gazebo_house"])
    input_mode = DeclareLaunchArgument("input_mode", default_value="ros", choices=["ros"])
    use_sim_time = DeclareLaunchArgument("use_sim_time", default_value="true")
    database = DeclareLaunchArgument(
        "database_path", default_value="/data/maps/gazebo_house/rtabmap.db"
    )

    gazebo = GzServer(world_sdf_file=str(world), create_own_container=True, verbosity_level=3)
    bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="tangying_gazebo_bridge",
        parameters=[{"config_file": str(bridge_config)}],
        output="screen",
    )
    # Gazebo's RGB-D sensor is mounted in the robot base model.  The static
    # transforms expose the same optical frames a real camera driver provides.
    transforms = [
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name=f"{camera}_camera_tf",
            arguments=[
                "--x", x,
                "--y", "0",
                "--z", z,
                "--roll", "-1.5707963",
                "--pitch", "0",
                "--yaw", "-1.5707963",
                "--frame-id", "base_link",
                "--child-frame-id", f"{camera}_camera_optical_frame",
            ],
            output="screen",
        )
        for camera, x, z in (("base", "0.28", "0.48"), ("head", "0.05", "1.05"))
    ]
    # The raw coloured clouds remain available to the user and are bridged at
    # camera resolution.  Nav2 gets a decimated geometric cloud so voxel
    # updates keep their deadline on a CPU-only CI/edge host; this is the same
    # RGB-D measurement, never Gazebo collision truth.
    nav_clouds = [
        Node(
            package="rtabmap_util",
            executable="point_cloud_xyz",
            name=f"{camera}_nav_cloud",
            output="screen",
            parameters=[
                {
                    "use_sim_time": True,
                    "decimation": 4,
                    "voxel_size": 0.03,
                    "max_depth": 5.0,
                    "approx_sync": True,
                    "approx_sync_max_interval": 0.1,
                    "qos": 2,
                    "qos_camera_info": 2,
                }
            ],
            remappings=[
                ("depth/image", f"/camera/{camera}/depth/image_raw"),
                ("depth/camera_info", f"/camera/{camera}/rgb/camera_info"),
                ("cloud", f"/camera/{camera}/nav_points"),
            ],
        )
        for camera in ("base", "head")
    ]
    navigation = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(share / "launch/navigation.launch.py")),
        launch_arguments={
            "mode": LaunchConfiguration("mode"),
            "scene": LaunchConfiguration("scene"),
            "input_mode": LaunchConfiguration("input_mode"),
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "database_path": LaunchConfiguration("database_path"),
            "odom_topic": "/odom",
            "cmd_vel_topic": "/cmd_vel",
            "base_rgb_topic": "/camera/base/rgb/image_raw",
            "base_depth_topic": "/camera/base/depth/image_raw",
            "base_camera_info_topic": "/camera/base/rgb/camera_info",
            "base_points_topic": "/camera/base/nav_points",
            "head_rgb_topic": "/camera/head/rgb/image_raw",
            "head_depth_topic": "/camera/head/depth/image_raw",
            "head_camera_info_topic": "/camera/head/rgb/camera_info",
            "head_points_topic": "/camera/head/nav_points",
        }.items(),
    )
    # The included launch declares the same generic arguments.  Seed the
    # configurations before it is visited so its defaults cannot silently
    # switch this backend back to the runtime bridge/tabletop profile.
    forced_config = [
        SetLaunchConfiguration("scene", "gazebo_house"),
        SetLaunchConfiguration("input_mode", "ros"),
        SetLaunchConfiguration("use_sim_time", "true"),
        SetLaunchConfiguration("odom_topic", "/odom"),
        SetLaunchConfiguration("cmd_vel_topic", "/cmd_vel"),
    ]
    backend_env = SetEnvironmentVariable("TANGYING_NAVIGATION_SCENE", "gazebo_house")
    return LaunchDescription([
        mode, database, scene, input_mode, use_sim_time,
        backend_env, gazebo, bridge, *transforms, *nav_clouds, *forced_config, navigation,
    ])
