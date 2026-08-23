"""Deterministic MJCF namespacing and scene composition.

The integration intentionally works at the MJCF boundary.  RoboCasa owns the
kitchen arena while AgentOS can attach any robot whose MJCF can be namespaced.
That same boundary is used later by real-robot adapters: neither Harness nor
Fleet needs to know how a specific simulator represents a robot.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tangying_robocasa.model_identity import model_content_hash

_REFERENCE_ATTRIBUTES = frozenset(
    {
        "actuator",
        "body",
        "body1",
        "body2",
        "camera",
        "geom",
        "geom1",
        "geom2",
        "hfield",
        "joint",
        "joint1",
        "joint2",
        "material",
        "mesh",
        "objname",
        "site",
        "site1",
        "site2",
        "target",
        "tendon",
        "texture",
    }
)
_MERGED_SECTIONS = (
    "asset",
    "contact",
    "equality",
    "tendon",
    "sensor",
    "actuator",
)


@dataclass(frozen=True, slots=True)
class ComposedScene:
    """Immutable serialized scene plus its stable identity and semantic names."""

    xml: str
    model_hash: str
    names: tuple[str, ...]
    scene_id: str


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class SceneConfig:
    """Inputs that deterministically identify the phase-one handoff scene."""

    scene_id: str = "robocasa-handoff-v1"
    layout_id: int = 1
    style_id: int = 1
    seed: int = 7
    robot_mjcf: Path = _PROJECT_ROOT / "sim/mujoco/assets/xlerobot/xlerobot.xml"


def _resolve_asset_files(root: ET.Element, source_dir: Path) -> None:
    compiler = root.find("compiler")
    mesh_dir = Path(compiler.get("meshdir", ".")) if compiler is not None else Path(".")
    texture_dir = (
        Path(compiler.get("texturedir", ".")) if compiler is not None else Path(".")
    )
    for element in root.iter():
        file_name = element.get("file")
        if not file_name or Path(file_name).is_absolute():
            continue
        if element.tag == "mesh":
            base = mesh_dir
        elif element.tag == "texture":
            base = texture_dir
        else:
            base = Path(".")
        element.set("file", str((source_dir / base / file_name).resolve()))


def prefix_mjcf(root: ET.Element, prefix: str, source_dir: Path) -> ET.Element:
    """Deep-copy an MJCF tree and namespace every declared name and reference.

    Asset paths are made absolute before the compiler element is discarded by a
    parent composer.  This is what allows robot MJCF files from unrelated source
    trees to coexist in one MuJoCo model.
    """

    if not prefix:
        raise ValueError("MJCF prefix must not be empty")
    result = copy.deepcopy(root)
    _resolve_asset_files(result, Path(source_dir))

    declared_names = {
        value
        for element in result.iter()
        if (value := element.get("name")) is not None
    }
    declared_classes = {
        value
        for element in result.iter("default")
        if (value := element.get("class")) is not None
    }

    for element in result.iter():
        name = element.get("name")
        if name is not None:
            element.set("name", f"{prefix}{name}")
        for attribute in _REFERENCE_ATTRIBUTES:
            value = element.get(attribute)
            if value in declared_names:
                element.set(attribute, f"{prefix}{value}")
        for attribute in ("class", "childclass"):
            value = element.get(attribute)
            if value in declared_classes:
                element.set(attribute, f"{prefix}{value}")

    model_name = result.get("model")
    if model_name:
        result.set("model", f"{prefix}{model_name}")
    return result


def _append_section_children(
    destination: ET.Element, source: ET.Element, section_name: str
) -> None:
    source_section = source.find(section_name)
    if source_section is None:
        return
    destination_section = destination.find(section_name)
    if destination_section is None:
        destination_section = ET.SubElement(destination, section_name)
    destination_section.extend(copy.deepcopy(list(source_section)))


def _append_defaults(destination: ET.Element, source: ET.Element) -> None:
    source_default = source.find("default")
    if source_default is None:
        return
    destination_default = destination.find("default")
    if destination_default is None:
        destination_default = ET.SubElement(destination, "default")
    destination_default.extend(copy.deepcopy(list(source_default)))


def _append_world(destination: ET.Element, source: ET.Element, *, offset_y: float) -> None:
    source_world = source.find("worldbody")
    if source_world is None:
        return
    destination_world = destination.find("worldbody")
    if destination_world is None:
        destination_world = ET.SubElement(destination, "worldbody")
    for element in copy.deepcopy(list(source_world)):
        if element.tag == "body":
            position = [float(value) for value in element.get("pos", "0 0 0").split()]
            position[1] += offset_y
            element.set("pos", " ".join(f"{value:.9g}" for value in position))
        destination_world.append(element)


def _collect_and_validate_names(root: ET.Element) -> tuple[str, ...]:
    # MuJoCo names are unique per object type, not globally. A texture and its
    # material commonly share a name, as do an arm body, mesh and actuator.
    section_namespaces = {"actuator", "contact", "equality", "sensor", "tendon"}
    tag_namespaces = {"freejoint": "joint"}
    qualified: list[tuple[str, str]] = []
    raw_names: list[str] = []
    for section in root:
        forced_namespace = section.tag if section.tag in section_namespaces else None
        for element in section.iter():
            if element is section:
                continue
            name = element.get("name")
            if name is None:
                continue
            namespace = forced_namespace or tag_namespaces.get(element.tag, element.tag)
            qualified.append((namespace, name))
            raw_names.append(name)
    duplicates = sorted(
        f"{namespace}:{name}"
        for (namespace, name), count in Counter(qualified).items()
        if count > 1
    )
    if duplicates:
        raise ValueError(f"duplicate MJCF names: {', '.join(duplicates)}")
    return tuple(dict.fromkeys(raw_names))


def _append_robot_to_scene(
    root: ET.Element,
    robot: ET.Element,
    *,
    position: tuple[float, float, float],
    quaternion: tuple[float, float, float, float],
) -> None:
    _append_defaults(root, robot)
    for section in _MERGED_SECTIONS:
        _append_section_children(root, robot, section)

    source_world = robot.find("worldbody")
    destination_world = root.find("worldbody")
    if source_world is None or destination_world is None:
        raise ValueError("robot and scene must both define a worldbody")
    chassis_seen = False
    for element in copy.deepcopy(list(source_world)):
        if element.tag == "body" and element.get("name", "").endswith("__chassis"):
            element.set("pos", " ".join(str(value) for value in position))
            element.set("quat", " ".join(str(value) for value in quaternion))
            chassis_seen = True
        destination_world.append(element)
    if not chassis_seen:
        raise ValueError("XLeRobot MJCF has no namespaced chassis body")


def _add_xlerobot_scene_anchors(robot: ET.Element) -> None:
    """Supply target bodies that the reusable robot fragment expects from a scene.

    The upstream XLeRobot fragment's head camera targets ``front_tray``; its
    original tabletop wrapper declares that target outside the robot file. A
    composed kitchen has no such wrapper, so the anchor belongs to this adapter.
    """

    declared = {element.get("name") for element in robot.iter() if element.get("name")}
    missing_targets = {
        camera.get("target")
        for camera in robot.iter("camera")
        if camera.get("target") and camera.get("target") not in declared
    }
    if not missing_targets:
        return
    if missing_targets != {"front_tray"}:
        raise ValueError(f"unsupported XLeRobot scene targets: {sorted(missing_targets)}")
    chassis = robot.find(".//body[@name='chassis']")
    if chassis is None:
        raise ValueError("XLeRobot MJCF has no chassis for scene anchors")
    ET.SubElement(chassis, "body", {"name": "front_tray", "pos": "0.30 0 0.755"})


def _add_handoff_semantics(root: ET.Element) -> None:
    world = root.find("worldbody")
    if world is None:
        raise ValueError("RoboCasa task XML has no worldbody")

    zones = (
        ("left-start-zone", "1.15 -0.62 0.955", "0.12 0.12 0.002", "0.10 0.35 0.95 0.35"),
        ("handoff-zone", "2.00 -0.62 0.955", "0.12 0.12 0.002", "0.95 0.70 0.10 0.35"),
        ("right-target-zone", "2.65 -0.62 0.955", "0.12 0.12 0.002", "0.12 0.45 0.95 0.45"),
    )
    for name, position, size, color in zones:
        ET.SubElement(
            world,
            "site",
            {
                "name": name,
                "type": "box",
                "pos": position,
                "size": size,
                "rgba": color,
                "group": "4",
            },
        )

    block = ET.SubElement(world, "body", {"name": "red-block", "pos": "1.15 -0.62 1.01"})
    ET.SubElement(block, "freejoint", {"name": "red-block-joint"})
    ET.SubElement(
        block,
        "geom",
        {
            "name": "red-block-geom",
            "type": "box",
            "size": "0.035 0.035 0.035",
            "mass": "0.08",
            "friction": "1 0.01 0.001",
            "rgba": "0.90 0.04 0.04 1",
        },
    )

    cameras = (
        ("overview", "2.75 -4.5 3.8", "1 0 0 0 0.65 0.76"),
        ("robot-1-evidence", "0.9 -2.2 1.65", "1 0 0 0 0.75 0.66"),
        ("robot-2-evidence", "2.9 -2.2 1.65", "1 0 0 0 0.75 0.66"),
    )
    for name, position, axes in cameras:
        ET.SubElement(
            world,
            "camera",
            {"name": name, "mode": "fixed", "pos": position, "xyaxes": axes, "fovy": "55"},
        )


def compose_handoff_scene(config: SceneConfig | None = None) -> ComposedScene:
    """Compose a deterministic RoboCasa kitchen with two XLeRobots.

    Imports stay inside this function so the main AgentOS test environment does
    not need the heavyweight RoboCasa dependency set.
    """

    if config is None:
        config = SceneConfig()

    import numpy as np
    from robocasa.models.scenes import KitchenArena
    from robosuite.models.tasks import ManipulationTask

    # RoboCasa correctly accepts a Generator for layout choices, but a few
    # robosuite visual/debug attributes still use NumPy's legacy global RNG.
    # Scope that legacy seed and restore the caller state so model identity is
    # deterministic without creating a hidden process-wide side effect.
    legacy_random_state = np.random.get_state()
    try:
        np.random.seed(config.seed)
        arena = KitchenArena(
            layout_id=config.layout_id,
            style_id=config.style_id,
            rng=np.random.default_rng(config.seed),
            clutter_mode=0,
        )
        task = ManipulationTask(
            mujoco_arena=arena,
            mujoco_robots=[],
            mujoco_objects=list(arena.fixtures.values()),
        )
    finally:
        np.random.set_state(legacy_random_state)
    root = ET.fromstring(task.get_xml())
    root.set("model", config.scene_id)

    robot_path = Path(config.robot_mjcf).resolve()
    robot_source = ET.parse(robot_path).getroot()
    _add_xlerobot_scene_anchors(robot_source)
    robot_1 = prefix_mjcf(robot_source, "robot-1__", robot_path.parent)
    robot_2 = prefix_mjcf(robot_source, "robot-2__", robot_path.parent)
    _append_robot_to_scene(
        root,
        robot_1,
        position=(1.15, -1.15, 0.035),
        quaternion=(0.707108, 0.0, 0.0, 0.707108),
    )
    _append_robot_to_scene(
        root,
        robot_2,
        position=(2.65, -1.15, 0.035),
        quaternion=(0.707108, 0.0, 0.0, 0.707108),
    )
    _add_handoff_semantics(root)

    names = _collect_and_validate_names(root)
    xml = ET.tostring(root, encoding="unicode")
    return ComposedScene(
        xml=xml,
        model_hash=model_content_hash(root),
        names=names,
        scene_id=config.scene_id,
    )


def compose_fixture_scene_for_test(robot_xml: str, source_dir: Path) -> ComposedScene:
    """Build a small two-robot world used to prove composition without RoboCasa."""

    source = ET.fromstring(robot_xml)
    robots = (
        (prefix_mjcf(source, "robot-1__", source_dir), -0.35),
        (prefix_mjcf(source, "robot-2__", source_dir), 0.35),
    )
    root = ET.Element("mujoco", {"model": "robocasa-handoff-fixture"})
    ET.SubElement(root, "compiler", {"angle": "radian"})
    ET.SubElement(root, "option", {"gravity": "0 0 -9.81", "timestep": "0.002"})
    ET.SubElement(root, "asset")
    ET.SubElement(root, "default")
    world = ET.SubElement(root, "worldbody")
    ET.SubElement(world, "light", {"pos": "0 0 3", "dir": "0 0 -1"})
    ET.SubElement(world, "geom", {"name": "floor", "type": "plane", "size": "3 3 .1"})

    for robot, offset_y in robots:
        _append_defaults(root, robot)
        for section in _MERGED_SECTIONS:
            _append_section_children(root, robot, section)
        _append_world(root, robot, offset_y=offset_y)

    block = ET.SubElement(world, "body", {"name": "red-block", "pos": "0 0 0.05"})
    ET.SubElement(block, "freejoint", {"name": "red-block-joint"})
    ET.SubElement(
        block,
        "geom",
        {
            "name": "red-block-geom",
            "type": "box",
            "size": ".025 .025 .025",
            "rgba": "0.85 0.05 0.05 1",
        },
    )

    names = _collect_and_validate_names(root)
    xml = ET.tostring(root, encoding="unicode")
    return ComposedScene(
        xml=xml,
        model_hash=model_content_hash(root),
        names=names,
        scene_id="fixture-handoff-v1",
    )
