"""Export resolved MJCF visual trees as self-contained articulated GLBs."""

from __future__ import annotations

import hashlib
import math
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

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

_SUPPORTED_GEOMS = frozenset({"box", "sphere", "cylinder", "capsule", "plane", "mesh"})


@dataclass(frozen=True, slots=True)
class ExportedGLB:
    path: Path
    sha256: str
    node_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JointBinding:
    node: str
    axis: tuple[float, float, float]
    direction: float
    offset: float
    minimum: float
    maximum: float


def _floats(value: str | None, default: tuple[float, ...]) -> tuple[float, ...]:
    if value is None:
        return default
    return tuple(float(part) for part in value.split())


def _quaternion_matrix(quaternion: tuple[float, ...]) -> np.ndarray:
    if len(quaternion) != 4:
        raise ValueError(f"MJCF quaternion must contain four values, got {quaternion!r}")
    w, x, y, z = quaternion
    length = math.sqrt(w * w + x * x + y * y + z * z)
    if length <= np.finfo(float).eps:
        raise ValueError("MJCF quaternion must not be zero")
    w, x, y, z = (component / length for component in (w, x, y, z))
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
            [0, 0, 0, 1],
        ],
        dtype=float,
    )


def _element_transform(
    attributes: Mapping[str, str], *, angle_scale: float, euler_sequence: str
) -> np.ndarray:
    import trimesh

    transform = np.eye(4)
    transform[:3, 3] = _floats(attributes.get("pos"), (0.0, 0.0, 0.0))
    if "quat" in attributes:
        transform[:3, :3] = _quaternion_matrix(_floats(attributes["quat"], ()))[:3, :3]
    elif "euler" in attributes:
        euler = tuple(value * angle_scale for value in _floats(attributes["euler"], ()))
        if len(euler) != 3:
            raise ValueError(f"MJCF euler must contain three values, got {euler!r}")
        rotation = trimesh.transformations.euler_matrix(*euler, axes=f"r{euler_sequence.lower()}")
        transform[:3, :3] = rotation[:3, :3]
    elif "axisangle" in attributes:
        axisangle = _floats(attributes["axisangle"], ())
        if len(axisangle) != 4:
            raise ValueError("MJCF axisangle must contain four values")
        rotation = trimesh.transformations.rotation_matrix(
            axisangle[3] * angle_scale, axisangle[:3]
        )
        transform[:3, :3] = rotation[:3, :3]
    return transform


def _default_attributes(root: ET.Element, tag: str) -> dict[str, dict[str, str]]:
    """Resolve MJCF default-class inheritance for one element kind."""

    resolved: dict[str, dict[str, str]] = {"": {}}

    def visit(element: ET.Element, inherited: dict[str, str], fallback_class: str) -> None:
        current = dict(inherited)
        template = element.find(tag)
        if template is not None:
            current.update(template.attrib)
        class_name = element.get("class", fallback_class)
        resolved[class_name] = current
        if not class_name:
            resolved[""] = current
        for child in element.findall("default"):
            visit(child, current, class_name)

    for default in root.findall("default"):
        visit(default, {}, "")
    return resolved


def _effective_attributes(
    element: ET.Element,
    defaults: Mapping[str, Mapping[str, str]],
    inherited_class: str | None,
) -> dict[str, str]:
    class_name = element.get("class", inherited_class or "")
    attributes = dict(defaults.get(class_name, defaults.get("", {})))
    attributes.update(element.attrib)
    return attributes


def _assets(root: ET.Element, tag: str) -> dict[str, dict[str, str]]:
    return {
        element.attrib["name"]: dict(element.attrib)
        for element in root.findall(f"./asset/{tag}")
        if element.get("name")
    }


def _rgba(
    attributes: Mapping[str, str], materials: Mapping[str, Mapping[str, str]]
) -> tuple[float, float, float, float]:
    source = attributes.get("rgba")
    if source is None and (material_name := attributes.get("material")):
        source = materials.get(material_name, {}).get("rgba")
    rgba = _floats(source, (0.5, 0.5, 0.5, 1.0))
    if len(rgba) != 4:
        raise ValueError(f"MJCF rgba must contain four values, got {rgba!r}")
    return tuple(float(np.clip(value, 0.0, 1.0)) for value in rgba)  # type: ignore[return-value]


def _material_for(
    mesh: Any,
    attributes: Mapping[str, str],
    materials: Mapping[str, Mapping[str, str]],
    textures: Mapping[str, Mapping[str, str]],
    rgba: tuple[float, float, float, float],
) -> None:
    from PIL import Image
    from trimesh.visual.material import PBRMaterial
    from trimesh.visual.texture import TextureVisuals

    material_name = attributes.get("material")
    material_attributes = materials.get(material_name or "", {})
    texture_attributes = textures.get(material_attributes.get("texture", ""), {})
    texture_path = texture_attributes.get("file")
    image = None
    if texture_path:
        path = Path(texture_path)
        if not path.is_file():
            raise FileNotFoundError(f"resolved MJCF texture does not exist: {path}")
        with Image.open(path) as source:
            image = source.convert("RGBA").copy()

    shininess = float(material_attributes.get("shininess", 0.0))
    material = PBRMaterial(
        name=material_name,
        baseColorFactor=list(rgba),
        baseColorTexture=image,
        metallicFactor=0.0,
        roughnessFactor=float(np.clip(1.0 - shininess, 0.0, 1.0)),
        alphaMode="BLEND" if rgba[3] < 1.0 else "OPAQUE",
        doubleSided=True,
    )
    vertices = np.asarray(mesh.vertices)
    if len(vertices):
        span = np.ptp(vertices[:, :2], axis=0)
        span[span == 0] = 1.0
        uv = (vertices[:, :2] - vertices[:, :2].min(axis=0)) / span
    else:
        uv = np.empty((0, 2))
    mesh.visual = TextureVisuals(uv=uv, material=material)


def _fromto_transform(attributes: Mapping[str, str]) -> tuple[np.ndarray | None, float | None]:
    import trimesh

    if "fromto" not in attributes:
        return None, None
    values = _floats(attributes["fromto"], ())
    if len(values) != 6:
        raise ValueError("MJCF fromto must contain six values")
    start, end = np.asarray(values[:3]), np.asarray(values[3:])
    delta = end - start
    length = float(np.linalg.norm(delta))
    if length <= np.finfo(float).eps:
        raise ValueError("MJCF fromto endpoints must differ")
    transform = trimesh.geometry.align_vectors((0.0, 0.0, 1.0), delta / length)
    transform[:3, 3] = (start + end) / 2.0
    return transform, length


def _geometry(
    attributes: Mapping[str, str], meshes: Mapping[str, Mapping[str, str]]
) -> tuple[Any, np.ndarray | None]:
    import trimesh

    geom_type = attributes.get("type", "sphere")
    size = _floats(attributes.get("size"), ())
    fromto, fromto_length = _fromto_transform(attributes)
    if geom_type == "box":
        if len(size) < 3:
            raise ValueError("MJCF box geom needs three size values")
        mesh = trimesh.creation.box(extents=np.asarray(size[:3]) * 2.0)
    elif geom_type == "sphere":
        if not size:
            raise ValueError("MJCF sphere geom needs a radius")
        mesh = trimesh.creation.icosphere(subdivisions=2, radius=size[0])
    elif geom_type == "cylinder":
        if not size or (len(size) < 2 and fromto_length is None):
            raise ValueError("MJCF cylinder geom needs radius and half-length or fromto")
        height = fromto_length if fromto_length is not None else size[1] * 2.0
        mesh = trimesh.creation.cylinder(radius=size[0], height=height, sections=32)
    elif geom_type == "capsule":
        if not size or (len(size) < 2 and fromto_length is None):
            raise ValueError("MJCF capsule geom needs radius and half-length or fromto")
        height = fromto_length if fromto_length is not None else size[1] * 2.0
        mesh = trimesh.creation.capsule(radius=size[0], height=height, count=(16, 16))
    elif geom_type == "plane":
        if len(size) < 2:
            raise ValueError("MJCF plane geom needs two size values")
        half_x = size[0] if size[0] > 0 else 1.0
        half_y = size[1] if size[1] > 0 else 1.0
        thickness = max(size[2] if len(size) > 2 else 0.001, 0.001)
        mesh = trimesh.creation.box(extents=(half_x * 2.0, half_y * 2.0, thickness))
    elif geom_type == "mesh":
        mesh_name = attributes.get("mesh")
        mesh_attributes = meshes.get(mesh_name or "")
        if mesh_attributes is None:
            raise ValueError(f"MJCF geom references unknown mesh {mesh_name!r}")
        file_name = mesh_attributes.get("file")
        if not file_name:
            raise ValueError(f"MJCF mesh {mesh_name!r} has no resolved file")
        path = Path(file_name)
        if not path.is_file():
            raise FileNotFoundError(f"resolved MJCF mesh does not exist: {path}")
        mesh = trimesh.load_mesh(
            path, file_type=path.suffix.lstrip("."), force="mesh", process=False
        )
        scale = _floats(mesh_attributes.get("scale"), (1.0, 1.0, 1.0))
        if len(scale) == 1:
            scale = scale * 3
        if len(scale) != 3:
            raise ValueError(f"MJCF mesh scale must contain one or three values: {scale!r}")
        mesh.apply_scale(scale)
    else:  # pragma: no cover - guarded by caller
        raise ValueError(f"unsupported MJCF geom type: {geom_type}")
    return mesh, fromto


def _unique_name(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    index = 2
    while f"{base}-{index}" in used:
        index += 1
    result = f"{base}-{index}"
    used.add(result)
    return result


def export_mjcf_visual(
    root: ET.Element,
    output: Path,
    body_filter: Callable[[str], bool],
) -> ExportedGLB:
    """Export supported, visible MJCF geoms while preserving articulation nodes."""

    import trimesh

    compiler = root.find("compiler")
    angle_scale = (
        math.pi / 180.0 if compiler is not None and compiler.get("angle") == "degree" else 1.0
    )
    euler_sequence = compiler.get("eulerseq", "xyz") if compiler is not None else "xyz"
    if len(euler_sequence) != 3 or any(axis.lower() not in "xyz" for axis in euler_sequence):
        raise ValueError(f"unsupported MJCF eulerseq: {euler_sequence!r}")

    geom_defaults = _default_attributes(root, "geom")
    materials = _assets(root, "material")
    textures = _assets(root, "texture")
    meshes = _assets(root, "mesh")
    scene = trimesh.Scene(base_frame="world")
    nodes = {"world"}
    used_names = {"world"}
    anonymous_body_index = 0
    anonymous_geom_index = 0
    anonymous_joint_index = 0

    def add_geom(element: ET.Element, parent: str, inherited_class: str | None) -> None:
        nonlocal anonymous_geom_index
        attributes = _effective_attributes(element, geom_defaults, inherited_class)
        geom_type = attributes.get("type", "sphere")
        class_name = attributes.get("class", inherited_class or "")
        if geom_type not in _SUPPORTED_GEOMS:
            return
        if attributes.get("group") == "3" or "collision" in class_name.lower():
            return
        rgba = _rgba(attributes, materials)
        if rgba[3] <= 0.0:
            return
        mesh, fromto = _geometry(attributes, meshes)
        _material_for(mesh, attributes, materials, textures, rgba)
        transform = (
            fromto
            if fromto is not None
            else _element_transform(
                attributes, angle_scale=angle_scale, euler_sequence=euler_sequence
            )
        )
        anonymous_geom_index += 1
        base_name = attributes.get("name") or f"{parent}:geom:{anonymous_geom_index}"
        node_name = _unique_name(base_name, used_names)
        scene.add_geometry(
            mesh,
            node_name=node_name,
            geom_name=node_name,
            parent_node_name=parent,
            transform=transform,
        )
        nodes.add(node_name)

    def add_body(element: ET.Element, parent: str, inherited_class: str | None) -> None:
        nonlocal anonymous_body_index, anonymous_joint_index
        anonymous_body_index += 1
        source_name = element.get("name") or f"body:{anonymous_body_index}"
        if not body_filter(source_name):
            return
        body_name = _unique_name(source_name, used_names)
        scene.graph.update(
            frame_to=body_name,
            frame_from=parent,
            matrix=_element_transform(
                element.attrib, angle_scale=angle_scale, euler_sequence=euler_sequence
            ),
        )
        nodes.add(body_name)
        articulated_parent = body_name
        for joint in element.findall("joint"):
            anonymous_joint_index += 1
            joint_name = joint.get("name") or f"{body_name}:joint:{anonymous_joint_index}"
            joint_name = _unique_name(joint_name, used_names)
            scene.graph.update(
                frame_to=joint_name,
                frame_from=articulated_parent,
                matrix=np.eye(4),
            )
            nodes.add(joint_name)
            articulated_parent = joint_name
        child_class = element.get("childclass", inherited_class)
        for geom in element.findall("geom"):
            add_geom(geom, articulated_parent, child_class)
        for child in element.findall("body"):
            add_body(child, articulated_parent, child_class)

    worldbody = root.find("worldbody")
    if worldbody is None:
        raise ValueError("MJCF root has no worldbody")
    for geom in worldbody.findall("geom"):
        add_geom(geom, "world", None)
    for body in worldbody.findall("body"):
        add_body(body, "world", None)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    blob = trimesh.exchange.gltf.export_glb(scene, include_normals=True)
    output.write_bytes(blob)
    return ExportedGLB(
        path=output,
        sha256=hashlib.sha256(blob).hexdigest(),
        node_names=tuple(sorted(nodes)),
    )


def build_xlerobot_binding(root: ET.Element) -> dict[str, JointBinding]:
    """Build the complete canonical XLeRobot telemetry-to-GLB joint table."""

    compiler = root.find("compiler")
    angle_scale = (
        math.pi / 180.0 if compiler is not None and compiler.get("angle") == "degree" else 1.0
    )
    joints: dict[str, tuple[ET.Element, ET.Element]] = {}
    for body in root.iter("body"):
        for joint in body.findall("joint"):
            if name := joint.get("name"):
                joints[name] = (body, joint)

    missing = [canonical for canonical, source in CANONICAL_JOINTS.items() if source not in joints]
    if missing:
        raise ValueError(f"XLeRobot MJCF is missing canonical joints: {', '.join(missing)}")

    bindings: dict[str, JointBinding] = {}
    for canonical, source_name in CANONICAL_JOINTS.items():
        _body, joint = joints[source_name]
        raw_axis = np.asarray(_floats(joint.get("axis"), (0.0, 0.0, 1.0)), dtype=float)
        if raw_axis.shape != (3,):
            raise ValueError(f"joint {source_name!r} axis must contain three values")
        length = float(np.linalg.norm(raw_axis))
        if length <= np.finfo(float).eps:
            raise ValueError(f"joint {source_name!r} axis must not be zero")
        raw_axis /= length
        first = next(component for component in raw_axis if abs(component) > 1e-12)
        direction = -1.0 if first < 0 else 1.0
        axis = raw_axis * direction
        limits = _floats(joint.get("range"), ())
        if len(limits) != 2:
            raise ValueError(f"joint {source_name!r} must declare a two-value range")
        joint_type = joint.get("type", "hinge")
        limit_scale = angle_scale if joint_type == "hinge" else 1.0
        bindings[canonical] = JointBinding(
            node=source_name,
            axis=tuple(float(value) for value in axis),
            direction=direction,
            offset=0.0,
            minimum=limits[0] * limit_scale,
            maximum=limits[1] * limit_scale,
        )
    return bindings
