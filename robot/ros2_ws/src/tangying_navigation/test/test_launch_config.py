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
