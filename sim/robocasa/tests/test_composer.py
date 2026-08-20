from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import mujoco
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
