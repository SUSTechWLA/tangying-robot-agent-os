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
