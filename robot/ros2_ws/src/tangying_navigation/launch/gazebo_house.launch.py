"""Run the Gazebo Harmonic household world against the normal ROS2 boundary.

This launch file deliberately includes the same navigation launch used by a
physical robot.  Only the sensor/actuator source changes: Gazebo publishes the
RGB-D, odometry and command topics through ros_gz_bridge, while RTAB-Map and
Nav2 remain the production path.
"""

import hashlib
import math
import os
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
    world = Path(os.environ.get("TANGYING_GAZEBO_WORLD") or share / "worlds/tangying_home.sdf")
    from tangying_navigation.gazebo_scenes import compose_scene
    selected_scene = os.environ.get("TANGYING_GAZEBO_SCENE", "home")
    if not os.environ.get("TANGYING_GAZEBO_WORLD"):
        world = compose_scene(selected_scene, world, Path("/tmp/tangying-scene.sdf"),
                              furnished_world=Path("/assets/aws-small-house-harmonic.sdf"))
    if not world.is_file():
        raise ValueError(f"Gazebo world does not exist: {world}")
    # The world's own content hash, and the identity every map surveyed in it is
    # pinned to. Derived here rather than typed into a terminal, because a revision
    # a human has to remember to update is a revision that will be wrong: two
    # different worlds would share a map identity, and a map surveyed through walls
    # that have since moved would load as though it were current.
    world_revision = hashlib.sha256(world.read_bytes()).hexdigest()
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
        "database_path", default_value=str(Path(os.environ.get("TANGYING_GAZEBO_MAP_NAMESPACE", "/data/maps/gazebo_house")) / world_revision[:16] / "rtabmap.db")
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
    # Where each camera is bolted and how far it is aimed down, in metres and
    # degrees, relative to base_link.
    #
    # The base camera is the *mapping* camera and its tilt is not cosmetic: the
    # reference robot puts it low and 15 degrees down so it can see the floor, and
    # a level camera instead measures walls - which is what this world shipped
    # with, and why no frame ever registered. Deriving the optical rotation from
    # the tilt keeps the two in step; hard-coding a level frame is how the mount
    # and the sensor drifted apart in the first place.
    camera_mounts = (("base", 0.36, 0.0, 0.16, 15.0), ("head", -0.1, 0.0, 1.05, 25.0))

    def optical_rpy(tilt_degrees: float) -> tuple[str, str, str]:
        """RPY of the camera's optical frame, given how far it is aimed down.

        REP-103 says a camera link is x-forward/y-left/z-up and the optical frame
        is x-right/y-down/z-forward; a level camera is therefore (-90, 0, -90) and
        aiming down by t adds t to the roll. Computed rather than typed so the
        number cannot silently disagree with the `<pose>` in the world file.
        """
        return (f"{-math.pi / 2 - math.radians(tilt_degrees):.7f}", "0",
                f"{-math.pi / 2:.7f}")

    transforms = [
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name=f"{camera}_camera_tf",
            arguments=[
                "--x", str(x),
                "--y", str(y),
                "--z", str(z),
                "--roll", optical_rpy(tilt)[0],
                "--pitch", optical_rpy(tilt)[1],
                "--yaw", optical_rpy(tilt)[2],
                "--frame-id", "base_link",
                "--child-frame-id", f"{camera}_camera_optical_frame",
            ],
            output="screen",
        )
        for camera, x, y, z, tilt in camera_mounts
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
            "cmd_vel_topic": "/navigation/cmd_vel",
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
        SetLaunchConfiguration("cmd_vel_topic", "/navigation/cmd_vel"),
    ]
    backend_env = SetEnvironmentVariable("TANGYING_NAVIGATION_SCENE", "gazebo_house")
    # The robot.profile.v1 runtime, started with the stack instead of by hand.
    #
    # It is what makes Gazebo a *backend*: the agent dials this port and does not
    # learn that the other end is a simulator. It used to be launched by a
    # `docker exec` recipe written down in a document, which meant a stack could
    # come up looking healthy while nothing could be observed or driven through it.
    #
    # The navigation URL is what turns on the mapping service catalogue; without it
    # the node deliberately stays observation-only and says so.
    runtime_env = [
        # Derived, not remembered: the calibration is a function of the world, so a
        # caller with the world file should never have to be told what it hashes to.
        SetEnvironmentVariable("TANGYING_GAZEBO_CALIBRATION_REVISION", world_revision),
        SetEnvironmentVariable("TANGYING_GAZEBO_WORLD_REVISION", world_revision),
        SetEnvironmentVariable(
            "TANGYING_NAVIGATION_URL",
            os.environ.get("TANGYING_NAVIGATION_URL", "http://127.0.0.1:18790"),
        ),
    ]
    runtime = Node(
        package="tangying_navigation",
        executable="gazebo_runtime",
        name="tangying_gazebo_runtime",
        output="screen",
    )
    return LaunchDescription([
        mode, database, scene, input_mode, use_sim_time,
        backend_env, gazebo, bridge, *transforms, *nav_clouds, *forced_config,
        *runtime_env, navigation, runtime,
    ])
