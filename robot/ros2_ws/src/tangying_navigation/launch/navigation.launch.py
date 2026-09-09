"""Explicit mapping/localization; never delete an existing RTAB-Map database."""

from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def launch_nodes(context):
    mode = LaunchConfiguration("mode").perform(context)
    scene = LaunchConfiguration("scene").perform(context)
    input_mode = LaunchConfiguration("input_mode").perform(context)
    if input_mode not in {"runtime", "ros"}:
        raise ValueError("input_mode must be runtime or ros")
    actuation_mode = "native_http" if input_mode == "runtime" else "ros_driver"
    topic = lambda key: LaunchConfiguration(key).perform(context)
    cmd_vel = "/tangying/navigation/cmd_vel" if input_mode == "runtime" else topic("cmd_vel_topic")
    database = Path(LaunchConfiguration("database_path").perform(context)).expanduser()
    if mode not in {"mapping", "localization"}:
        raise ValueError("mode must be mapping or localization")
    if mode == "localization" and (not database.is_file() or database.stat().st_size == 0):
        raise ValueError("localization requires an existing, nonempty RTAB-Map database")
    database.parent.mkdir(parents=True, exist_ok=True)
    share = Path(get_package_share_directory("tangying_navigation"))
    params = yaml.safe_load((share / "config/nav2.yaml").read_text())
    if scene == "home":
        home_profile = yaml.safe_load((share / "config/home_rtabmap.yaml").read_text())
        if home_profile.get("scene") != "home":
            raise ValueError("home RTAB-Map profile has an invalid scene marker")
    elif scene != "tabletop":
        raise ValueError("scene must be tabletop or home")
    params["bt_navigator"]["ros__parameters"]["odom_topic"] = topic("odom_topic")
    for camera in ("base", "head"):
        for role in ("mark", "clear"):
            params["local_costmap"]["local_costmap"]["ros__parameters"]["obstacles"][
                f"{camera}_{role}"
            ]["topic"] = topic(f"{camera}_points_topic")
    with NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="tangying-nav2-", delete=False
    ) as output:
        yaml.safe_dump(params, output)
        params = output.name
    rtab_parameters = {
                "use_sim_time": False,
                "frame_id": "base_link",
                "odom_frame_id": "odom",
                "map_frame_id": "map",
                "publish_tf": True,
                "subscribe_depth": True,
                "subscribe_odom_info": False,
                "approx_sync": False,
                "qos_image": 2,
                "qos_camera_info": 2,
                "topic_queue_size": 10,
                "sync_queue_size": 10,
                "database_path": str(database),
                "map_always_update": True,
                "Mem/IncrementalMemory": "true" if mode == "mapping" else "false",
                "Mem/InitWMWithAllNodes": "false" if mode == "mapping" else "true",
                "Mem/DepthCompressionFormat": ".png",
                "Mem/BadSignaturesIgnored": "true",
                "Mem/NotLinkedNodesKept": "false",
                "Rtabmap/StartNewMapOnGoodSignature": "true",
                "Kp/MaxFeatures": "250",
                "RGBD/LinearUpdate": "0.02",
                "RGBD/AngularUpdate": "0.03",
                "RGBD/OptimizeFromGraphEnd": "false",
                "Rtabmap/DetectionRate": "2.0",
                "Grid/Sensor": "1",
                "Reg/Force3DoF": "true",
                "Optimizer/GravitySigma": "0",
                # RTAB-Map declares string parameters for Grid/* options;
                # passing a Python bool makes rclcpp abort before mapping.
                "Grid/3D": "false",
                "Grid/CellSize": "0.025",
                "Grid/RangeMin": "0.08",
                "Grid/RangeMax": "5.0",
                "Grid/MaxGroundHeight": "0.03",
                "Grid/MinGroundHeight": "-0.05",
                "Grid/MaxObstacleHeight": "2.0",
                "Grid/NormalsSegmentation": "true",
                "Grid/RayTracing": "true",
            }
    if scene == "home":
        rtab_parameters.update(home_profile.get("rgbd", {}))
    rtabmap = Node(
        package="rtabmap_slam",
        executable="rtabmap",
        namespace="rtabmap",
        output="screen",
        parameters=[rtab_parameters],
        remappings=[
            ("rgb/image", topic("base_rgb_topic")),
            ("depth/image", topic("base_depth_topic")),
            ("rgb/camera_info", topic("base_camera_info_topic")),
            ("odom", topic("odom_topic")),
            ("map", "/map"),
        ],
    )
    names = ["controller_server", "planner_server", "bt_navigator"]
    nodes = [rtabmap]
    if input_mode == "runtime":
        remaps = [("/odom", topic("odom_topic"))]
        for camera in ("base", "head"):
            remaps.extend(
                [
                    (f"/camera/{camera}/rgb/image_raw", topic(f"{camera}_rgb_topic")),
                    (f"/camera/{camera}/depth/image_raw", topic(f"{camera}_depth_topic")),
                    (f"/camera/{camera}/rgb/camera_info", topic(f"{camera}_camera_info_topic")),
                    (f"/camera/{camera}/points", topic(f"{camera}_points_topic")),
                ]
            )
        nodes.insert(
            0,
            Node(
                package="tangying_navigation",
                executable="runtime_rgbd_bridge",
                output="screen",
                remappings=remaps,
            ),
        )
    for package, executable in [
        ("nav2_controller", "controller_server"),
        ("nav2_planner", "planner_server"),
        ("nav2_bt_navigator", "bt_navigator"),
    ]:
        extra = (
            {"default_nav_to_pose_bt_xml": str(share / "config/navigate.xml")}
            if executable == "bt_navigator"
            else {}
        )
        if executable == "controller_server":
            extra["enable_stamped_cmd_vel"] = input_mode == "runtime"
        nodes.append(
            Node(
                package=package,
                executable=executable,
                name=executable,
                output="screen",
                parameters=[params, extra],
                remappings=[("cmd_vel", cmd_vel), ("odom", topic("odom_topic"))],
            )
        )
    nodes.extend(
        [
            Node(
                package="nav2_lifecycle_manager",
                executable="lifecycle_manager",
                name="lifecycle_manager_navigation",
                output="screen",
                parameters=[{"use_sim_time": False, "autostart": True, "node_names": names}],
            ),
            Node(
                package="tangying_navigation",
                executable="navigation_http",
                output="screen",
                parameters=[
                    {
                        "mode": mode,
                        "actuation_mode": actuation_mode,
                        "cmd_vel_topic": cmd_vel,
                        "odom_topic": topic("odom_topic"),
                        "base_depth_topic": topic("base_depth_topic"),
                        "head_depth_topic": topic("head_depth_topic"),
                        "scene": scene,
                    }
                ],
            ),
        ]
    )
    return nodes


def generate_launch_description():
    arguments = [
        DeclareLaunchArgument("mode", default_value="mapping", choices=["mapping", "localization"]),
        DeclareLaunchArgument("scene", default_value="tabletop", choices=["tabletop", "home"]),
        DeclareLaunchArgument("input_mode", default_value="runtime", choices=["runtime", "ros"]),
        DeclareLaunchArgument("database_path", default_value="/data/maps/rtabmap.db"),
        DeclareLaunchArgument("odom_topic", default_value="/odom"),
        DeclareLaunchArgument("cmd_vel_topic", default_value="/cmd_vel"),
    ]
    for camera in ("base", "head"):
        for suffix, topic_suffix in [
            ("rgb", "rgb/image_raw"),
            ("depth", "depth/image_raw"),
            ("camera_info", "rgb/camera_info"),
            ("points", "points"),
        ]:
            arguments.append(
                DeclareLaunchArgument(
                    f"{camera}_{suffix}_topic", default_value=f"/camera/{camera}/{topic_suffix}"
                )
            )
    return LaunchDescription(arguments + [OpaqueFunction(function=launch_nodes)])
