import mujoco
import numpy as np
import pytest
from tangying_sim.model import load_task_model
from tangying_sim.rgbd_navigation import load_navigation_model
from tangying_sim.rgbd_runtime import RgbdRuntimeService, RgbdTabletopWorld
from tangying_sim.rgbd_workcell import BIN_DIMENSIONS_M, WORKCELL_REVISION


def bounds(model, data, geom):
    index = geom if isinstance(geom, int) else model.geom(geom).id
    half = np.abs(data.geom_xmat[index].reshape(3, 3)) @ model.geom_size[index]
    return data.geom_xpos[index]-half, data.geom_xpos[index]+half


def test_navigation_workcell_has_supported_fixtures_and_full_footprint_clearance():
    model = load_navigation_model()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    low, high = bounds(model, data, "tabletop")
    assert low[1] == pytest.approx(.35)
    assert low[1] - (.05+.22) >= .0799
    assert WORKCELL_REVISION == "supported-navigation-workcell-v2"
    for name in ("left_bin", "right_bin", "front_tray"):
        body = model.body(name).id
        for geom in np.flatnonzero(model.geom_bodyid == body):
            fixture_low, fixture_high = bounds(model, data, int(geom))
            assert np.all(fixture_low[:2] >= low[:2]-1e-8)
            assert np.all(fixture_high[:2] <= high[:2]+1e-8)
            assert fixture_low[2] == pytest.approx(high[2])
            assert fixture_low[1]-(.05+.22) >= .0799
        if name.endswith("bin"):
            np.testing.assert_allclose(model.geom_size[np.flatnonzero(model.geom_bodyid == body)[0], :2]*2,
                                       BIN_DIMENSIONS_M)
    for end in ("front", "back"):
        for side in ("left", "right"):
            leg_low, leg_high = bounds(model, data, f"table_leg_{end}_{side}")
            assert np.all(leg_low[:2] >= low[:2]-1e-8)
            assert np.all(leg_high[:2] <= high[:2]+1e-8)
            assert leg_low[2] == pytest.approx(0)
            assert leg_high[2] >= low[2]


def test_legacy_workcell_is_unchanged_by_rgbd_commissioning():
    legacy = load_task_model()
    assert legacy.geom("tabletop").size[1] == pytest.approx(.39)
    assert legacy.body("front_tray").pos[1] == pytest.approx(.31)
    assert legacy.geom("table_leg_front_left").pos[1] == pytest.approx(-.31)


def test_both_released_objects_remain_visually_supported_after_extended_settling(monkeypatch):
    monkeypatch.delenv("TANGYING_NAVIGATION_URL", raising=False)
    service = RgbdRuntimeService(RgbdTabletopWorld.seeded(7))
    try:
        world = service.world
        base = world.robot_state()["base_pose"]
        for name, destination in (("red-cup", "right-bin"), ("blue-bottle", "front-tray")):
            assert world.pick(name).success
            assert world.verify_grasp(name).success
            assert world.place(destination).success
            assert world.verify_inside(name, destination).success
        for _ in range(12):
            world._step(25)  # Another .6 s of real physics after both releases.
        for name, destination in (("red-cup", "right-bin"), ("blue-bottle", "front-tray")):
            assert world.verify_inside(name, destination).success
        _, _, state = service.capture_scene()
        assert state["base_pose"] == base
        assert state["perception"]["workcell_revision"] == WORKCELL_REVISION
    finally:
        service.close()
