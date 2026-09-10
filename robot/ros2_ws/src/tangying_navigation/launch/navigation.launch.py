"""Explicit mapping/localization; never delete an existing RTAB-Map database."""

import os
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
    use_sim_time = LaunchConfiguration("use_sim_time").perform(context).lower() in {"1", "true", "yes"}
    # The container entrypoint and Gazebo wrapper export the backend scene.
    # Honour it here because an included launch can redeclare generic
    # arguments and otherwise fall back to the runtime/tabletop defaults.
    scene_env = os.environ.get("TANGYING_NAVIGATION_SCENE", "")
    if scene_env in {"tabletop", "home", "home_task", "gazebo_house"}:
        scene = scene_env
    if scene == "gazebo_house":
        input_mode, use_sim_time = "ros", True
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
    for node_config in params.values():
        if isinstance(node_config, dict) and isinstance(node_config.get("ros__parameters"), dict):
            node_config["ros__parameters"]["use_sim_time"] = use_sim_time
    if scene in {"home", "home_task", "gazebo_house"}:
        profile_name = "gazebo_house_rtabmap.yaml" if scene == "gazebo_house" else "home_rtabmap.yaml"
        home_profile = yaml.safe_load((share / "config" / profile_name).read_text())
        if scene != "home_task" and home_profile.get("scene") != scene:
            raise ValueError(f"{scene} RTAB-Map profile has an invalid scene marker")
    elif scene != "tabletop":
        raise ValueError("scene must be tabletop, home, home_task or gazebo_house")
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
                "use_sim_time": use_sim_time,
                "frame_id": "base_link",
                "odom_frame_id": "odom",
                "map_frame_id": "map",
                "publish_tf": True,
                "subscribe_depth": True,
                # Keep the odometry contract explicit for launch wrappers and
                # drivers.  The RTAB-Map SLAM node resolves the pose from the
                # odom->base_link TF at the RGB-D timestamp; the wheel/visual
                # source remains replaceable without changing this node.
                "subscribe_odom": True,
                "subscribe_odom_info": False,
                # RGB-D drivers publish colour, depth and CameraInfo on
                # independent callbacks.  A small timestamp skew is normal
                # on both Gazebo and physical cameras, so approximate sync is
                # required for RTAB-Map to receive complete RGB-D tuples.
                "approx_sync": True,
                # Gazebo publishes odometry at 30 Hz and RGB-D at 15 Hz;
                # allow one camera period for the complete sensor tuple.
                "approx_sync_max_interval": 0.1,
                "qos_image": 2,
                "qos_camera_info": 2,
                "topic_queue_size": 10,
                "sync_queue_size": 10,
                "database_path": str(database),
                "map_always_update": True,
                "Mem/IncrementalMemory": "true" if mode == "mapping" else "false",
                "Mem/InitWMWithAllNodes": "false" if mode == "mapping" else "true",
                "Mem/DepthCompressionFormat": ".png",
                # Keep low-texture frames in the graph while the robot is
                # bootstrapping its map.  A first frame can legitimately have
                # few visual words; dropping it prevents the map from ever
                # acquiring a stable visual baseline in simulation.
                "Mem/BadSignaturesIgnored": "false",
                "Mem/NotLinkedNodesKept": "true",
                # Keep the first valid RGB-D signature while bootstrapping a
                # new household map.  Starting a new map on a "good"
                # signature discards the low-texture first frame and leaves
                # the dictionary empty in a static simulator.
                "Rtabmap/StartNewMapOnGoodSignature": "false",
                "Kp/MaxFeatures": "250",
                # ORB is available in the headless ROS image and remains
                # deterministic at the 320x240 RGB-D profile used by both
                # Gazebo and the physical camera contract.  The RTAB-Map
                # default (KAZE) produced zero words on the low-texture home
                # walls, which correctly kept readiness blocked but made the
                # simulator unable to exercise visual loop closure.
                "Kp/DetectorStrategy": "8",
                "Vis/FeatureType": "8",
                # Depth has already been normalized on the SLAM-only stream;
                # do not mask image keypoints just because a real camera (or
                # Gazebo) reports a no-return pixel at that location.
                "Vis/DepthAsMask": "false",
                "Mem/UseOdomFeatures": "false",
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
    if scene in {"home", "home_task", "gazebo_house"}:
        rtab_parameters.update(home_profile.get("rgbd", {}))
    nodes = []
    # Keep raw depth topics for the user view and Nav2 clearing.  RTAB-Map
    # consumes a derived stream where no-return pixels are set to the camera's
    # far range; this matches how a physical RGB-D driver is normalized while
    # avoiding fabricated obstacles in the raw PointCloud2 path.
    depth_topics = {}
    if input_mode == "ros":
        for camera in ("base", "head"):
            sanitized_topic = f"/camera/{camera}/depth/rtabmap"
            depth_topics[camera] = sanitized_topic
            nodes.append(
                Node(
                    package="tangying_navigation",
                    executable="depth_sanitizer",
                    name=f"{camera}_depth_sanitizer",
                    output="screen",
                    parameters=[
                        {
                            "input_topic": topic(f"{camera}_depth_topic"),
                            "output_topic": sanitized_topic,
                            "max_depth_m": 5.0,
                        }
                    ],
                )
            )
    rtabmap = Node(
        package="rtabmap_slam",
        executable="rtabmap",
        namespace="rtabmap",
        output="screen",
        parameters=[rtab_parameters],
        remappings=[
            ("rgb/image", topic("base_rgb_topic")),
            ("depth/image", depth_topics.get("base", topic("base_depth_topic"))),
            ("rgb/camera_info", topic("base_camera_info_topic")),
            ("odom", topic("odom_topic")),
            ("map", "/map"),
        ],
    )
    names = ["controller_server", "planner_server", "bt_navigator"]
    nodes.append(rtabmap)
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
                parameters=[{"use_sim_time": use_sim_time, "autostart": True, "node_names": names}],
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
                        "use_sim_time": use_sim_time,
                    }
                ],
            ),
        ]
    )
    return nodes


def generate_launch_description():
    arguments = [
        DeclareLaunchArgument("mode", default_value="mapping", choices=["mapping", "localization"]),
        DeclareLaunchArgument(
            "scene", default_value="tabletop", choices=["tabletop", "home", "home_task", "gazebo_house"]
        ),
        DeclareLaunchArgument("input_mode", default_value="runtime", choices=["runtime", "ros"]),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
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
