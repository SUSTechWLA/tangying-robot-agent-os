from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import mujoco
import numpy as np
import pytest
from tangying_robocasa.composer import (
    SceneConfig,
    compose_fixture_scene_for_test,
    compose_handoff_scene,
    prefix_mjcf,
)

FIXTURE_XML = """
<mujoco model="fixture-robot">
  <compiler meshdir="meshes" texturedir="textures"/>
  <asset>
    <texture name="skin" type="2d" file="skin.png"/>
    <material name="paint" texture="skin"/>
    <mesh name="base_mesh" file="base.obj"/>
  </asset>
  <default>
    <default class="robot">
      <geom material="paint"/>
      <default class="collision"><geom contype="1"/></default>
    </default>
  </default>
  <worldbody>
    <body name="chassis" childclass="robot">
      <joint name="wheel" type="hinge"/>
      <geom name="shell" type="mesh" mesh="base_mesh" class="collision"/>
      <site name="tool"/>
    </body>
  </worldbody>
  <sensor><jointpos name="wheel_position" joint="wheel"/></sensor>
  <actuator><motor name="wheel_motor" joint="wheel"/></actuator>
</mujoco>
"""


def _one_pixel_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(
            ">I", zlib.crc32(kind + data) & 0xFFFFFFFF
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


def test_prefix_mjcf_rewrites_names_references_classes_and_files(tmp_path: Path) -> None:
    mesh_dir = tmp_path / "meshes"
    texture_dir = tmp_path / "textures"
    mesh_dir.mkdir()
    texture_dir.mkdir()
    root = ET.fromstring(FIXTURE_XML)

    result = prefix_mjcf(root, "robot-1__", tmp_path)

    assert result.find(".//body").get("name") == "robot-1__chassis"
    assert result.find(".//motor").get("joint") == "robot-1__wheel"
    assert result.find(".//geom[@name='robot-1__shell']").get("mesh") == "robot-1__base_mesh"
    assert result.find(".//body").get("childclass") == "robot-1__robot"
    assert result.find(".//geom[@name='robot-1__shell']").get("class") == (
        "robot-1__collision"
    )
    assert Path(result.find(".//mesh").get("file")).is_absolute()
    assert Path(result.find(".//texture").get("file")).is_absolute()


def test_two_prefixed_robots_have_no_duplicate_names_and_compile(tmp_path: Path) -> None:
    (tmp_path / "meshes").mkdir()
    (tmp_path / "textures").mkdir()
    (tmp_path / "meshes" / "base.obj").write_text(
        "v 0 0 0\nv .1 0 0\nv 0 .1 0\nv 0 0 .1\n"
        "f 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n",
        encoding="utf-8",
    )
    (tmp_path / "textures" / "skin.png").write_bytes(_one_pixel_png())
    scene = compose_fixture_scene_for_test(FIXTURE_XML, tmp_path)

    assert len(scene.names) == len(set(scene.names))
    assert "robot-1__chassis" in scene.names
    assert "robot-2__chassis" in scene.names
    assert "red-block" in scene.names

    model = mujoco.MjModel.from_xml_string(scene.xml)
    assert model.nbody >= 4
    assert model.nu == 2


def test_fixture_scene_hash_is_independent_of_asset_checkout_path(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (left, right):
        (root / "meshes").mkdir(parents=True)
        (root / "textures").mkdir()
        (root / "meshes" / "base.obj").write_text(
            "v 0 0 0\nv .1 0 0\nv 0 .1 0\nv 0 0 .1\n"
            "f 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n",
            encoding="utf-8",
        )
        (root / "textures" / "skin.png").write_bytes(_one_pixel_png())

    left_scene = compose_fixture_scene_for_test(FIXTURE_XML, left)
    right_scene = compose_fixture_scene_for_test(FIXTURE_XML, right)

    assert left_scene.model_hash == right_scene.model_hash
    assert Path(ET.fromstring(left_scene.xml).find(".//mesh").get("file")).is_absolute()
    mujoco.MjModel.from_xml_string(left_scene.xml)
    mujoco.MjModel.from_xml_string(right_scene.xml)


@pytest.mark.robocasa
def test_real_robocasa_scene_contains_two_xlerobots_and_one_block() -> None:
    pytest.importorskip("robocasa")
    scene = compose_handoff_scene(SceneConfig())
    model = mujoco.MjModel.from_xml_string(scene.xml)

    body_names = {
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id)
        for body_id in range(model.nbody)
    }
    assert "robot-1__chassis" in body_names
    assert "robot-2__chassis" in body_names
    assert "red-block" in body_names
    assert model.nu >= 22
    assert len(scene.model_hash) == 64


@pytest.mark.robocasa
def test_rgbd_cameras_belong_to_the_correct_xlerobot_head() -> None:
    pytest.importorskip("robocasa")
    model = mujoco.MjModel.from_xml_string(compose_handoff_scene(SceneConfig(seed=7)).xml)
    for robot_id in ("robot-1", "robot-2"):
        camera_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_CAMERA, f"{robot_id}__rgbd_head"
        )
        tilt_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, f"{robot_id}__head_tilt_link"
        )
        assert camera_id >= 0
        body_id = int(model.cam_bodyid[camera_id])
        ancestors = set()
        while body_id > 0:
            ancestors.add(body_id)
            body_id = int(model.body_parentid[body_id])
        assert tilt_id in ancestors
    assert mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_CAMERA, "robot-1-evidence"
    ) == -1


@pytest.mark.robocasa
def test_operator_overview_camera_frames_both_robots_and_handoff_workspace() -> None:
    """The customer overview must show the task, not mostly foreground floor."""

    pytest.importorskip("robocasa")
    model = mujoco.MjModel.from_xml_string(compose_handoff_scene(SceneConfig(seed=7)).xml)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "overview")
    assert data.cam_xpos[camera_id].tolist() == pytest.approx([1.9, -3.3, 4.5])

    camera_to_world = data.cam_xmat[camera_id].reshape(3, 3)
    vertical_fov = np.deg2rad(float(model.cam_fovy[camera_id]))
    for body_name in ("robot-1__chassis", "robot-2__chassis", "red-block"):
        body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        camera_point = camera_to_world.T @ (
            data.xpos[body_id] - data.cam_xpos[camera_id]
        )
        assert camera_point[2] < 0, f"{body_name} is behind the overview camera"
        normalized_vertical = abs(camera_point[1] / -camera_point[2]) / np.tan(
            vertical_fov / 2
        )
        assert normalized_vertical < 0.82, f"{body_name} is too close to the frame edge"
    assert mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_CAMERA, "robot-2-evidence"
    ) == -1


@pytest.mark.robocasa
def test_head_pan_changes_only_its_own_rgbd_camera_transform() -> None:
    pytest.importorskip("robocasa")
    model = mujoco.MjModel.from_xml_string(compose_handoff_scene(SceneConfig(seed=7)).xml)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    camera_ids = {
        robot_id: mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_CAMERA, f"{robot_id}__rgbd_head"
        )
        for robot_id in ("robot-1", "robot-2")
    }
    before = {robot_id: data.cam_xmat[camera_id].copy() for robot_id, camera_id in camera_ids.items()}
    joint_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_JOINT, "robot-1__head_pan_joint"
    )
    data.qpos[int(model.jnt_qposadr[joint_id])] = 0.4
    mujoco.mj_forward(model, data)

    assert not np.allclose(data.cam_xmat[camera_ids["robot-1"]], before["robot-1"])
    assert np.allclose(data.cam_xmat[camera_ids["robot-2"]], before["robot-2"])


@pytest.mark.robocasa
def test_real_robocasa_model_hash_is_deterministic_for_fixed_seed() -> None:
    pytest.importorskip("robocasa")

    first = compose_handoff_scene(SceneConfig(seed=7))
    second = compose_handoff_scene(SceneConfig(seed=7))

    assert first.model_hash == second.model_hash
    assert first.xml == second.xml
