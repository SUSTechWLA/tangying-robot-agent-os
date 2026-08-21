from __future__ import annotations

import hashlib
import importlib
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
CANONICAL_JOINTS = {
    "joint.left.rotation": "Rotation_L",
    "joint.left.pitch": "Pitch_L",
    "joint.left.elbow": "Elbow_L",
    "joint.left.wrist_pitch": "Wrist_Pitch_L",
    "joint.left.wrist_roll": "Wrist_Roll_L",
    "joint.left.jaw": "Jaw_L",
    "joint.right.rotation": "Rotation_R",
    "joint.right.pitch": "Pitch_R",
    "joint.right.elbow": "Elbow_R",
    "joint.right.wrist_pitch": "Wrist_Pitch_R",
    "joint.right.wrist_roll": "Wrist_Roll_R",
    "joint.right.jaw": "Jaw_R",
    "joint.head.pan": "head_pan_joint",
    "joint.head.tilt": "head_tilt_joint",
}
CANONICAL_BODY_NODES = {
    "joint.left.rotation": "Rotation_Pitch",
    "joint.left.pitch": "Upper_Arm",
    "joint.left.elbow": "Lower_Arm",
    "joint.left.wrist_pitch": "Wrist_Pitch_Roll",
    "joint.left.wrist_roll": "Fixed_Jaw",
    "joint.left.jaw": "Moving_Jaw",
    "joint.right.rotation": "Rotation_Pitch_R",
    "joint.right.pitch": "Upper_Arm_2",
    "joint.right.elbow": "Lower_Arm_2",
    "joint.right.wrist_pitch": "Wrist_Pitch_Roll_2",
    "joint.right.wrist_roll": "Fixed_Jaw_2",
    "joint.right.jaw": "Moving_Jaw_2",
    "joint.head.pan": "head_pan_link",
    "joint.head.tilt": "head_tilt_link",
}


def _exporter():
    return importlib.import_module("tangying_robocasa.gltf_export")


def _articulated_fixture(tmp_path: Path) -> ET.Element:
    from PIL import Image

    texture_path = tmp_path / "paint.png"
    Image.new("RGBA", (2, 2), (255, 128, 32, 255)).save(texture_path)
    root = ET.fromstring(
        f"""
        <mujoco model="articulated-fixture">
          <compiler angle="radian"/>
          <asset>
            <texture name="paint-texture" type="2d" file="{texture_path}"/>
            <material name="paint" texture="paint-texture" rgba="0.2 0.4 0.6 1"/>
          </asset>
          <worldbody>
            <geom name="floor" type="plane" size="2 3 0.1" rgba="0.1 0.1 0.1 1"/>
            <body name="chassis" pos="1 2 3" quat="0.70710678 0 0 0.70710678">
              <geom name="painted-box" type="box" size="0.1 0.2 0.3" material="paint"/>
              <geom name="visible-cylinder" type="cylinder" size="0.05 0.2"
                    pos="0.5 0 0" quat="0.70710678 0.70710678 0 0"/>
              <geom name="visible-capsule" type="capsule" size="0.04"
                    fromto="0 0 0 0.3 0.4 0"/>
              <geom name="hidden-sphere" type="sphere" size="0.2" rgba="1 0 0 0"/>
              <geom name="collision-shape" type="capsule" size="0.1 0.3" group="3"/>
              <body name="filtered-body"><geom type="cylinder" size="0.1 0.2"/></body>
            </body>
          </worldbody>
        </mujoco>
        """
    )
    chassis = root.find("./worldbody/body[@name='chassis']")
    assert chassis is not None
    for index, (canonical, joint_name) in enumerate(CANONICAL_JOINTS.items()):
        body_name = CANONICAL_BODY_NODES[canonical]
        body = ET.SubElement(chassis, "body", {"name": body_name, "pos": f"0 0 {index / 100}"})
        axis = "0 -1 0" if canonical == "joint.left.rotation" else "0 0 1"
        minimum, maximum = ("-2.1", "2.1") if index == 0 else ("-1", "1")
        ET.SubElement(
            body,
            "joint",
            {"name": joint_name, "axis": axis, "range": f"{minimum} {maximum}"},
        )
        ET.SubElement(body, "geom", {"type": "sphere", "size": "0.01"})
    return root


def _textured_obj_fixture(tmp_path: Path) -> tuple[ET.Element, np.ndarray]:
    from PIL import Image

    texture_path = tmp_path / "mesh-texture.png"
    Image.new("RGBA", (2, 2), (32, 128, 255, 255)).save(texture_path)
    mesh_path = tmp_path / "textured-triangle.obj"
    mesh_path.write_text(
        "v 0 0 0\nv 2 0 0\nv 0 1 0\nvt 0.2 0.3\nvt 0.8 0.4\nvt 0.4 0.9\nf 1/1 2/2 3/3\n"
    )
    root = ET.fromstring(
        f"""
        <mujoco>
          <asset>
            <mesh name="textured-triangle" file="{mesh_path}"/>
            <texture name="mesh-texture" type="2d" file="{texture_path}"/>
            <material name="mesh-material" texture="mesh-texture"/>
          </asset>
          <worldbody>
            <body name="mesh-body">
              <geom name="textured-geom" type="mesh" mesh="textured-triangle"
                    material="mesh-material"/>
            </body>
          </worldbody>
        </mujoco>
        """
    )
    return root, np.asarray([[0.2, 0.3], [0.8, 0.4], [0.4, 0.9]])


def _resolved_xlerobot_root() -> ET.Element:
    source = ROOT / "sim/mujoco/assets/xlerobot/xlerobot.xml"
    root = ET.parse(source).getroot()
    asset_dir = source.parent / "assets"
    for mesh in root.findall("./asset/mesh"):
        file_name = mesh.get("file")
        assert file_name
        mesh.set("file", str((asset_dir / file_name).resolve()))
    return root


def test_export_keeps_named_joint_nodes_and_embeds_buffers_and_images(tmp_path: Path):
    module = _exporter()
    from pygltflib import GLTF2

    exported = module.export_mjcf_visual(
        _articulated_fixture(tmp_path),
        tmp_path / "nested" / "robot.glb",
        lambda name: name != "filtered-body",
    )
    blob = exported.path.read_bytes()
    gltf = GLTF2().load_binary(str(exported.path))
    names = {node.name for node in gltf.nodes}

    assert names >= {
        "chassis",
        "Rotation_L",
        "Upper_Arm",
        "painted-box",
        "visible-cylinder",
        "visible-capsule",
        "floor",
    }
    assert {"hidden-sphere", "collision-shape", "filtered-body"}.isdisjoint(names)
    assert all(not buffer.uri for buffer in gltf.buffers)
    assert gltf.images and all(
        not image.uri and image.bufferView is not None for image in gltf.images
    )
    assert exported.sha256 == hashlib.sha256(blob).hexdigest()
    assert exported.node_names == tuple(sorted(names))


def test_export_applies_mjcf_body_transform_and_material_rgba(tmp_path: Path):
    module = _exporter()
    from pygltflib import GLTF2

    exported = module.export_mjcf_visual(
        _articulated_fixture(tmp_path), tmp_path / "robot.glb", lambda _name: True
    )
    gltf = GLTF2().load_binary(str(exported.path))
    chassis = next(node for node in gltf.nodes if node.name == "chassis")
    colors = [material.pbrMetallicRoughness.baseColorFactor for material in gltf.materials]
    transform = np.asarray(chassis.matrix).reshape((4, 4), order="F")

    assert transform[:3, 3] == pytest.approx([1.0, 2.0, 3.0])
    np.testing.assert_allclose(
        transform[:3, :3],
        [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
        atol=1e-7,
    )
    assert any(color == pytest.approx([0.2, 0.4, 0.6, 1.0]) for color in colors)


def test_mjcf_without_compiler_uses_default_degree_angles(tmp_path: Path):
    module = _exporter()
    from pygltflib import GLTF2

    root = _articulated_fixture(tmp_path)
    compiler = root.find("compiler")
    assert compiler is not None
    root.remove(compiler)
    chassis = root.find("./worldbody/body[@name='chassis']")
    assert chassis is not None
    chassis.attrib.pop("quat")
    chassis.set("euler", "90 0 0")
    rotation = root.find(".//joint[@name='Rotation_L']")
    assert rotation is not None
    rotation.set("range", "-90 90")

    exported = module.export_mjcf_visual(root, tmp_path / "degrees.glb", lambda _name: True)
    gltf = GLTF2().load_binary(str(exported.path))
    chassis_node = next(node for node in gltf.nodes if node.name == "chassis")
    transform = np.asarray(chassis_node.matrix).reshape((4, 4), order="F")
    binding = module.build_xlerobot_binding(root)["joint.left.rotation"]

    np.testing.assert_allclose(
        transform[:3, :3],
        [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]],
        atol=1e-7,
    )
    assert binding.minimum == pytest.approx(-np.pi / 2)
    assert binding.maximum == pytest.approx(np.pi / 2)


def test_textured_mesh_preserves_native_obj_uvs(tmp_path: Path):
    module = _exporter()
    import trimesh

    root, expected_uv = _textured_obj_fixture(tmp_path)
    exported = module.export_mjcf_visual(root, tmp_path / "textured.glb", lambda _name: True)
    scene = trimesh.load(exported.path, force="scene", process=False)
    _transform, geometry_name = scene.graph.get("textured-geom")
    exported_uv = np.asarray(scene.geometry[geometry_name].visual.uv)

    np.testing.assert_allclose(exported_uv, expected_uv, atol=1e-7)


def test_binding_matches_all_canonical_telemetry_keys_and_joint_metadata(tmp_path: Path):
    module = _exporter()

    bindings = module.build_xlerobot_binding(_articulated_fixture(tmp_path))

    assert set(bindings) == set(CANONICAL_JOINTS)
    assert {canonical: binding.node for canonical, binding in bindings.items()} == (
        CANONICAL_BODY_NODES
    )
    assert bindings["joint.left.rotation"] == module.JointBinding(
        node="Rotation_Pitch",
        axis=(0.0, 1.0, 0.0),
        direction=-1.0,
        offset=0.0,
        minimum=-2.1,
        maximum=2.1,
    )
    assert bindings["joint.left.pitch"].node == "Upper_Arm"
    assert bindings["joint.head.tilt"].node == "head_tilt_link"


def test_binding_rejects_an_incomplete_xlerobot(tmp_path: Path):
    module = _exporter()
    root = _articulated_fixture(tmp_path)
    missing = root.find(".//joint[@name='Jaw_R']")
    assert missing is not None
    parent = next(body for body in root.iter("body") if missing in list(body))
    parent.remove(missing)

    with pytest.raises(ValueError, match=r"joint\.right\.jaw"):
        module.build_xlerobot_binding(root)


def test_body_binding_rejects_a_nonzero_canonical_joint_pivot(tmp_path: Path):
    module = _exporter()
    root = _articulated_fixture(tmp_path)
    rotation = root.find(".//joint[@name='Rotation_L']")
    assert rotation is not None
    rotation.set("pos", "0.01 0 0")

    with pytest.raises(ValueError, match=r"Rotation_L.*non-zero pivot"):
        module.build_xlerobot_binding(root)


def test_joint_proxy_is_a_leaf_marker_at_the_mjcf_pivot(tmp_path: Path):
    module = _exporter()
    from pygltflib import GLTF2

    root = ET.fromstring(
        """
        <mujoco>
          <worldbody>
            <body name="pivot-body">
              <joint name="pivot-joint" pos="0.1 0.2 0.3"/>
              <geom name="body-geom" type="box" size="0.1 0.1 0.1"/>
              <body name="child-body"><geom type="sphere" size="0.05"/></body>
            </body>
          </worldbody>
        </mujoco>
        """
    )
    exported = module.export_mjcf_visual(root, tmp_path / "pivot.glb", lambda _name: True)
    gltf = GLTF2().load_binary(str(exported.path))
    node_by_name = {node.name: node for node in gltf.nodes}
    proxy = node_by_name["pivot-joint"]
    body_children = {
        gltf.nodes[index].name for index in (node_by_name["pivot-body"].children or [])
    }
    proxy_transform = np.asarray(proxy.matrix).reshape((4, 4), order="F")

    assert proxy_transform[:3, 3] == pytest.approx([0.1, 0.2, 0.3])
    assert proxy.mesh is None
    assert not proxy.children
    assert {"pivot-joint", "body-geom", "child-body"} <= body_children


def test_real_xlerobot_exports_complete_visual_hierarchy_and_binding(tmp_path: Path):
    module = _exporter()
    from pygltflib import GLTF2

    root = _resolved_xlerobot_root()
    exported = module.export_mjcf_visual(root, tmp_path / "xlerobot.glb", lambda _name: True)
    gltf = GLTF2().load_binary(str(exported.path))
    names = {node.name for node in gltf.nodes}

    assert names >= {"chassis", "Rotation_L", "Pitch_L", "Upper_Arm", "head_tilt_joint"}
    assert all(not buffer.uri for buffer in gltf.buffers)
    assert all(not image.uri for image in gltf.images)
    bindings = module.build_xlerobot_binding(root)
    assert set(bindings) == set(CANONICAL_JOINTS)
    assert {canonical: binding.node for canonical, binding in bindings.items()} == (
        CANONICAL_BODY_NODES
    )
    assert {binding.node for binding in bindings.values()} <= names
