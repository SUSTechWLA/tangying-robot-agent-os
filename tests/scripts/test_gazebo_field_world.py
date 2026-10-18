"""Fixture construction checks; real physics acceptance runs in Gazebo separately."""

import itertools
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from sim.gazebo.build_field_world import SENSOR_POSE, build_world


def test_field_scene_has_physical_bodies_and_independent_measured_sensors():
    world = ET.fromstring(build_world()).find("world")
    assert world is not None
    assert float(world.findtext("physics/max_step_size")) == .002
    for name in ("ground-a", "ground-b", "uav-a"):
        model = world.find(f"model[@name='{name}']")
        assert model is not None and model.findtext("static", "false") == "false"
        assert float(model.findtext("link/inertial/mass")) > 0
        assert model.find("link/collision") is not None
        assert model.find("plugin").get("name") == "tangying::FieldController"
        sensor = model.find("link/sensor[@name='lidar']")
        assert sensor.get("type") == "gpu_lidar"
        assert int(sensor.findtext("lidar/scan/vertical/samples")) > 1
        assert tuple(map(float, sensor.findtext("pose").split())) == SENSOR_POSE
        assert model.find("link/sensor[@type='imu']") is not None
        assert model.find("link/sensor[@type='contact']") is not None
    assert world.findall("include") == []  # isolated run must not fetch Fuel models


def test_world_initialization_is_reproducible_and_configurable():
    first = build_world(robot_starts={"ground-a": (-1., 0., .16)})
    assert first == build_world(robot_starts={"ground-a": (-1., 0., .16)})
    model = ET.fromstring(first).find("world/model[@name='ground-a']")
    assert tuple(map(float, model.findtext("pose").split()))[:3] == (-1., 0., .16)


@pytest.mark.parametrize("kwargs", [
    {"horizontal_samples": 0}, {"vertical_samples": 1}, {"vertical_samples": 3.5},
    {"horizontal_samples": True}, {"robot_starts": {"unknown": (0, 0, 1)}},
    {"robot_starts": {"ground-a": (0, 0, float("nan"))}},
    {"robot_starts": {"uav-a": (10, 0, 1)}},
    {"robot_starts": {"ground-a": (0, 0, 1)}},
])
def test_reject_invalid_fixture_configuration(kwargs):
    with pytest.raises(ValueError):
        build_world(**kwargs)


@pytest.mark.parametrize("generated", [False, True])
def test_reported_collision_envelopes_cover_every_real_box_corner_in_three_dimensions(generated):
    source = Path(__file__).resolve().parents[2] / "sim/gazebo"
    controller = (source / "field_controller.cc").read_text()
    # Inspect the values actually published by the C++ plugin. A larger box in
    # either the generator or retained SDF must not silently outgrow its envelope.
    radii = {}
    for kind in ("Ground", "Aerial"):
        match = re.search(rf"constexpr double k{kind}BoundingRadius\s*=\s*([.0-9]+);", controller)
        assert match is not None, f"missing published {kind} bounding radius"
        radii[kind] = float(match.group(1))
    assert '"radius", aerial ? kAerialBoundingRadius : kGroundBoundingRadius' in controller
    world = ET.fromstring(build_world() if generated else (source / "field_validation.sdf").read_text())
    for robot, kind in (("ground-a", "Ground"), ("ground-b", "Ground"), ("uav-a", "Aerial")):
        model = world.find(f"world/model[@name='{robot}']")
        dimensions = tuple(map(float, model.findtext("link/collision/geometry/box/size").split()))
        for signs in itertools.product((-1, 1), repeat=3):
            corner = tuple(sign * side / 2 for sign, side in zip(signs, dimensions))
            assert math.sqrt(sum(x*x for x in corner)) <= radii[kind], (robot, corner, radii[kind])
        # Full 3D norms are rotation invariant, unlike an upright XY footprint.
        if kind == "Ground":
            assert math.sqrt(sum((x/2)**2 for x in dimensions)) > .33
