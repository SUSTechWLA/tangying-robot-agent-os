import math
from pathlib import Path

import yaml


def test_real_floor_returns_clear_rays_but_cannot_become_obstacle_hits():
    config = yaml.safe_load((Path(__file__).parents[1] / "config/nav2.yaml").read_text())
    layer = config["local_costmap"]["local_costmap"]["ros__parameters"]["obstacles"]
    for camera in ("base", "head"):
        mark, clear = layer[f"{camera}_mark"], layer[f"{camera}_clear"]
        assert mark["topic"] == clear["topic"]
        assert mark["marking"] and not mark["clearing"]
        assert clear["clearing"] and not clear["marking"]
        # ObservationBuffer filters before ray tracing. Keep actual floor depth
        # returns as clearing endpoints without promoting them to obstacles.
        assert clear["min_obstacle_height"] <= 0 <= clear["max_obstacle_height"]
        assert not mark["min_obstacle_height"] <= 0 <= mark["max_obstacle_height"]
    for key in ("local_costmap", "global_costmap"):
        assert config[key][key]["ros__parameters"]["track_unknown_space"] is True
    assert config["planner_server"]["ros__parameters"]["GridBased"]["allow_unknown"] is False


def test_dwb_scoring_resolves_the_goal_checkers_required_precision():
    config = yaml.safe_load((Path(__file__).parents[1] / "config/nav2.yaml").read_text())
    controller = config["controller_server"]["ros__parameters"]
    checker = controller["goal_checker"]
    planner = controller["FollowPath"]
    local = config["local_costmap"]["local_costmap"]["ros__parameters"]
    # Collision cells follow measured RGB-D ray density. A separate continuous
    # metric critic must resolve the target inside a coarse, zero-distance cell.
    assert local["resolution"] == .025
    assert "ContinuousGoal" in planner["critics"]
    assert "tangying_dwb_critics" in planner["default_critic_namespaces"]
    assert planner["critics"].index("ObstacleFootprint") < planner["critics"].index("ContinuousGoal")
    assert planner["ContinuousGoal.scale"] == planner["GoalDist.scale"] * .5
    assert planner["ContinuousGoal.activation_distance"] > math.sqrt(2) * local["resolution"]
    for soft_critic in ("GoalAlign", "PathAlign", "PathDist", "GoalDist"):
        assert planner[f"{soft_critic}.class"] == f"tangying_dwb_critics::Approach{soft_critic}Critic"
    for safety_critic in ("RotateToGoal", "Oscillation", "ObstacleFootprint"):
        assert safety_critic in planner["critics"]
        assert f"{safety_critic}.class" not in planner
        assert planner.get(f"{safety_critic}.scale", 1) > 0
    assert planner["xy_goal_tolerance"] == checker["xy_goal_tolerance"]
    # With endpoint scoring, zero can beat the next sampled velocity before
    # reaching a tighter goal checker. Bound the half-step position/yaw error.
    for axis in ("x", "y"):
        half_step = (planner[f"max_vel_{axis}"] - planner[f"min_vel_{axis}"]) / (
            2 * (planner[f"v{axis}_samples"] - 1)
        )
        assert half_step * planner["ContinuousGoal.lookahead_time"] < checker["xy_goal_tolerance"]
    assert 0 < planner["ContinuousGoal.lookahead_time"] <= planner["sim_time"]
    half_yaw_step = planner["max_vel_theta"] / (planner["vtheta_samples"] - 1)
    yaw_lookahead = planner["RotateToGoal.lookahead_time"]
    if yaw_lookahead < 0:
        yaw_lookahead = planner["sim_time"]
    assert half_yaw_step * yaw_lookahead < checker["yaw_goal_tolerance"]
    # Leave 10 mm of the independent 15 mm Runtime position budget for
    # map/odom disagreement rather than spending it all on control error.
    assert checker["xy_goal_tolerance"] <= .005
    assert checker["yaw_goal_tolerance"] <= .03


def test_home_rtabmap_profile_uses_both_rgbd_cameras_and_map_frame():
    config = yaml.safe_load((Path(__file__).parents[1] / "config/home_rtabmap.yaml").read_text())
    assert config["scene"] == "home"
    assert config["map_frame"] == "map"
    assert config["base_rgb_topic"].endswith("/camera/base/rgb/image_raw")
    assert config["base_depth_topic"].endswith("/camera/base/depth/image_raw")
    assert config["head_rgb_topic"].endswith("/camera/head/rgb/image_raw")
    assert config["head_depth_topic"].endswith("/camera/head/depth/image_raw")
    assert config["mapping_database"].endswith("home/rtabmap.db")
    # RTAB-Map declares Grid/* options as strings. YAML booleans make rclcpp
    # abort during launch before the first frame, so keep the profile typed.
    assert all(isinstance(config["rgbd"][key], str) for key in (
        "Grid/3D", "Reg/Force3DoF", "Grid/CellSize", "Grid/RangeMin", "Grid/RangeMax",
        "Grid/MaxObstacleHeight", "Grid/MinGroundHeight", "Grid/MaxGroundHeight",
    ))


def test_gazebo_house_profile_matches_physical_ros_topic_contract():
    root = Path(__file__).parents[1]
    config = yaml.safe_load((root / "config/gazebo_house_rtabmap.yaml").read_text())
    assert config["scene"] == "gazebo_house"
    assert config["mapping_database"].endswith("gazebo_house/rtabmap.db")
    for camera in ("base", "head"):
        assert config[f"{camera}_rgb_topic"] == f"/camera/{camera}/rgb/image_raw"
        assert config[f"{camera}_depth_topic"] == f"/camera/{camera}/depth/image_raw"
        assert config[f"{camera}_points_topic"] == f"/camera/{camera}/points"
    assert config["rgbd"]["approx_sync"] is True


def test_navigation_uses_headless_compatible_gftt_orb_features_for_rgbd_slam():
    launch = (Path(__file__).parents[1] / "launch/navigation.launch.py").read_text()
    assert '"Kp/DetectorStrategy": "8"' in launch
    assert '"Vis/FeatureType": "8"' in launch
    assert '"Vis/DepthAsMask": "false"' in launch
    assert '"Mem/UseOdomFeatures": "false"' in launch
    assert '"approx_sync": True' in launch
    assert '"subscribe_odom": True' in launch
    assert 'executable="depth_sanitizer"' in launch
    assert '"/camera/{camera}/depth/rtabmap"' in launch


def test_gazebo_house_world_has_real_sensor_and_actuator_streams():
    root = Path(__file__).parents[1]
    world = (root / "worlds/tangying_home.sdf").read_text()
    assert '<world name="tangying_home">' in world
    assert world.count('type="rgbd_camera"') == 2
    assert 'name="gz::sim::systems::DiffDrive"' in world
    assert '<topic>/camera/base</topic>' in world
    assert '<topic>/camera/head</topic>' in world
    assert '<odom_topic>/odom</odom_topic>' in world
    assert '<topic>/cmd_vel</topic>' in world
    # The world is not allowed to smuggle ground-truth pose or room metadata
    # into the ROS boundary; only the sensor/actuator topics are bridged.
    bridge = yaml.safe_load((root / "config/gazebo_house_bridge.yaml").read_text())
    topics = {item["ros_topic_name"] for item in bridge}
    assert topics == {
        "/clock", "/odom", "/tf", "/cmd_vel",
        "/camera/base/rgb/image_raw", "/camera/base/depth/image_raw",
        "/camera/base/rgb/camera_info", "/camera/base/points",
        "/camera/head/rgb/image_raw", "/camera/head/depth/image_raw",
        "/camera/head/rgb/camera_info", "/camera/head/points",
    }
    assert all(item.get("qos_profile") == "SENSOR_DATA"
               for item in bridge if item["ros_topic_name"].startswith("/camera/"))


def test_navigation_entrypoint_routes_gazebo_house_to_simulator_launch():
    entrypoint = (Path(__file__).parents[5] / "deploy/robot/navigation/entrypoint.sh").read_text()
    assert "tabletop|home|gazebo_house" in entrypoint
    assert 'gazebo_house.launch.py' in entrypoint
    assert 'TANGYING_NAVIGATION_SCENE:-tabletop' in entrypoint
    launch = (Path(__file__).parents[1] / "launch/gazebo_house.launch.py").read_text()
    assert 'executable="point_cloud_xyz"' in launch
    assert 'nav_points' in launch
