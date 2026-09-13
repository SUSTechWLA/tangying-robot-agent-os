import time
from dataclasses import replace

import numpy as np
import pytest
from tangying_robot_gateway.rgbd import RgbdFrame
from tangying_sim.household_perception import HouseholdRgbdPerception


def household_frame(*, cup=(2.275, 3.415), tray=(2.46,3.335), second_tray=None, hollow=True, duplicate=False, sequence=1, stamp=None):
    """Synthetic camera surfaces; deliberately no MuJoCo/semantic input."""
    height = width = 200
    row, col = np.indices((height, width))
    camera_z, camera_x, camera_y, focal = 1.33, 2.38, 3.46, 200
    depth = np.full((height, width), camera_z-.73)

    def surface(z, predicate):
        d = camera_z-z
        x = camera_x+(col-99.5)*d/focal
        y = camera_y-(row-99.5)*d/focal
        mask = predicate(x, y)
        depth[mask] = np.minimum(depth[mask], d)

    for target in (tray,second_tray):
        if target is not None:
            surface(.83, lambda x, y, target=target: (np.abs(x-target[0]) < .07) & (np.abs(y-target[1]) < .075)
                    & ((np.abs(x-target[0]) > .06) | (np.abs(y-target[1]) > .065) | (not hollow)))
    if cup is not None:
        surface(.85, lambda x, y: (x-cup[0])**2+(y-cup[1])**2 < .045**2)
    if duplicate:
        surface(.85, lambda x, y: (x-2.55)**2+(y-3.60)**2 < .045**2)
    transform = np.diag([1., -1., -1., 1.])
    transform[:3, 3] = [camera_x, camera_y, camera_z]
    return RgbdFrame("robot", "head", "optical", "calibration",
                     int(time.time()*1000) if stamp is None else stamp, sequence,
                     np.full((height, width, 3), 125, dtype=np.uint8), depth,
                     np.array([[focal, 0, 99.5], [0, focal, 99.5], [0, 0, 1]]), transform)


def test_household_recognition_uses_metric_shape_not_colour_or_simulator_state():
    frame = household_frame()
    first = HouseholdRgbdPerception().reconstruct(frame)
    recoloured = np.random.default_rng(1).integers(0, 255, frame.rgb.shape, dtype=np.uint8)
    second = HouseholdRgbdPerception().reconstruct(replace(frame, rgb=recoloured))
    assert {e.entity_id for e in first.entities} == {"ceramic-mug", "kitchen-tray"}
    assert [(e.entity_id, e.pose) for e in first.entities] == [(e.entity_id, e.pose) for e in second.entities]
    assert all("color" not in e.attributes for e in first.entities)
    mug = next(e for e in first.entities if e.entity_id == "ceramic-mug")
    assert mug.category == "cup"
    assert mug.pose[:3] == pytest.approx([2.275, 3.415, .79], abs=.01)


def test_same_shape_instances_are_not_silently_assigned_one_mug_identity():
    scene = HouseholdRgbdPerception().reconstruct(household_frame(duplicate=True))
    assert "ceramic-mug" not in {e.entity_id for e in scene.entities}


def test_cup_touching_tray_has_pixel_grounded_placement_relation():
    perception = HouseholdRgbdPerception()
    now = int(time.time()*1000)
    perception.reconstruct(household_frame(stamp=now-50))
    scene = perception.reconstruct(household_frame(cup=(2.46, 3.335), stamp=now, sequence=2))
    mug = next(e for e in scene.entities if e.entity_id == "ceramic-mug")
    assert mug.relation == "inside:kitchen-tray"


def test_robot_first_return_mask_cannot_create_an_object():
    frame = household_frame()
    scene = HouseholdRgbdPerception().reconstruct(frame, robot_mask=np.ones(frame.depth_m.shape, dtype=np.uint8))
    assert not scene.entities


def test_closed_gripper_near_supported_cup_does_not_prove_a_grasp():
    scene = HouseholdRgbdPerception().reconstruct(
        household_frame(), end_effectors={"right": [2.275, 3.415, .79]},
        grippers={"right": "closed"},
    )
    mug = next(e for e in scene.entities if e.entity_id == "ceramic-mug")
    assert not mug.relation


def test_blank_depth_clears_recent_tray_evidence():
    perception = HouseholdRgbdPerception()
    frame = household_frame()
    assert perception.reconstruct(frame).entities
    blank = replace(frame, depth_m=np.zeros_like(frame.depth_m), sequence=2)
    assert not perception.reconstruct(blank).entities


@pytest.fixture(params=["household_post_placement", "household_post_placement_fragmented"])
def frozen_placement(monkeypatch, request):
    import hashlib
    import json
    from pathlib import Path
    root = Path(__file__).with_name("fixtures") / request.param
    metadata = json.loads(root.with_suffix(".json").read_text())
    assert hashlib.sha256(root.with_suffix(".npz").read_bytes()).hexdigest() == metadata["fixtureSHA256"]
    data = np.load(root.with_suffix(".npz"))
    clock = [int(metadata["capturedAtUnixMs"])]
    monkeypatch.setattr(time,"time",lambda:clock[0]/1000)
    frame = RgbdFrame("robot",metadata["sourceId"],"optical",metadata["transformRevision"],
        clock[0],int(metadata["sequence"]),np.full((*data["depth_m"].shape,3),125,dtype=np.uint8),
        data["depth_m"],data["intrinsics"],data["world_from_camera"])
    return frame,data["robot_mask"],clock


def test_current_partial_rim_observes_placement_without_recent_tray_history(frozen_placement):
    frame,mask,clock = frozen_placement
    perception = HouseholdRgbdPerception()
    for index in range(3):
        clock[0] = frame.captured_at_unix_ms+index*2500
        current = replace(frame,captured_at_unix_ms=clock[0],sequence=frame.sequence+index)
        scene = perception.reconstruct(current,robot_mask=mask)
        objects = {entity.entity_id:entity for entity in scene.entities}
        assert objects.keys() == {"ceramic-mug","kitchen-tray"}
        assert objects["ceramic-mug"].relation == "inside:kitchen-tray"
        assert objects["kitchen-tray"].pose[:3] == pytest.approx([2.46,3.335,.73],abs=.012)


@pytest.mark.parametrize("missing",["blank","rim","opposite_edges","support"])
def test_current_missing_surfaces_cannot_reuse_frozen_placement(frozen_placement,missing):
    from tangying_robot_gateway.rgbd import deproject
    frame,mask,clock = frozen_placement
    perception = HouseholdRgbdPerception()
    assert any(e.relation == "inside:kitchen-tray" for e in perception.reconstruct(frame,robot_mask=mask).entities)
    points,_ = deproject(frame)
    depth = frame.depth_m.copy()
    if missing == "blank":
        depth[:] = 0
    elif missing == "support":
        depth[np.abs(points[:,:,2]-.73)<.015] = 0
    else:
        remove = np.abs(points[:,:,2]-.83)<.012
        if missing == "opposite_edges":
            remove &= points[:,:,0] > 2.41
        depth[remove] = 0
    clock[0] += 100
    current = replace(frame,depth_m=depth,captured_at_unix_ms=clock[0],sequence=frame.sequence+1)
    scene = perception.reconstruct(current,robot_mask=mask)
    assert not any(e.entity_id == "kitchen-tray" or e.relation == "inside:kitchen-tray" for e in scene.entities)


def test_stale_frozen_capture_never_proves_placement(frozen_placement):
    frame,mask,clock = frozen_placement
    clock[0] += 2001
    with pytest.raises(ValueError,match="stale"):
        HouseholdRgbdPerception().reconstruct(frame,robot_mask=mask)


def test_current_rim_fit_has_no_absolute_xy_pose_prior(frozen_placement):
    frame,mask,_ = frozen_placement
    shifted = frame.world_from_camera.copy()
    shifted[:2,3] += [.04,.02]
    scene = HouseholdRgbdPerception().reconstruct(replace(frame,world_from_camera=shifted),robot_mask=mask)
    tray = next(e for e in scene.entities if e.entity_id == "kitchen-tray")
    assert tray.pose[:3] == pytest.approx([2.50,3.355,.73],abs=.012)
    assert next(e for e in scene.entities if e.entity_id == "ceramic-mug").relation == "inside:kitchen-tray"


def test_moved_tray_is_measured_at_new_position_and_does_not_contain_old_cup():
    perception = HouseholdRgbdPerception()
    first = household_frame(cup=(2.46,3.335))
    assert next(e for e in perception.reconstruct(first).entities if e.category == "cup").relation == "inside:kitchen-tray"
    current = household_frame(cup=(2.46,3.335),tray=(2.46,3.515),sequence=2)
    scene = perception.reconstruct(current)
    tray = next(e for e in scene.entities if e.entity_id == "kitchen-tray")
    assert tray.pose[:2] == pytest.approx([2.46,3.515],abs=.012)
    assert not next(e for e in scene.entities if e.category == "cup").relation


def test_two_observed_rectangular_trays_are_ambiguous():
    for center in ((2.25,3.38),(2.49,3.55)):
        single = HouseholdRgbdPerception().reconstruct(household_frame(cup=None,tray=center))
        assert "kitchen-tray" in {e.entity_id for e in single.entities}
    scene = HouseholdRgbdPerception().reconstruct(household_frame(cup=None,tray=(2.25,3.38),second_tray=(2.49,3.55)))
    assert "kitchen-tray" not in {e.entity_id for e in scene.entities}


def test_solid_horizontal_rectangle_is_not_a_hollow_tray_rim():
    scene = HouseholdRgbdPerception().reconstruct(household_frame(cup=None,hollow=False))
    assert "kitchen-tray" not in {e.entity_id for e in scene.entities}


@pytest.mark.parametrize("axis", [0, 1])
def test_isolated_far_points_cannot_remove_an_observed_edge(axis):
    from tangying_sim.household_perception import _observed_edge
    cloud = np.zeros((8, 3))
    cloud[:, 1-axis] = [0, .005, .010, .015, .025, .100, .101, .102]
    assert _observed_edge(cloud[:5], axis)
    assert _observed_edge(cloud, axis)


@pytest.mark.parametrize("axis", [0, 1])
def test_disconnected_subthreshold_fragments_cannot_combine_into_an_edge(axis):
    from tangying_sim.household_perception import _observed_edge
    cloud = np.zeros((9, 3))
    # The first span is long enough but has too few samples; the second has
    # enough samples but is too short. Their union must not satisfy both tests.
    cloud[:, 1-axis] = [0, .01, .02, .03, .10, .101, .102, .103, .104]
    assert not _observed_edge(cloud, axis)
