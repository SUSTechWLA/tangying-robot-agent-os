"""Gazebo scene composition. Geometry initializes physics; it is never perception.

Each named scene has a distinct SDF hash and map namespace. The task fixtures are
commissioning geometry, not a claim that grasp controllers are already validated.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

SCENES = ("home", "tabletop", "home_task", "home_furnished")


def _box(world, name, pose, size, colour):
    model = ET.SubElement(world, "model", name=name)
    ET.SubElement(model, "static").text = "true"
    ET.SubElement(model, "pose").text = " ".join(map(str, [*pose, 0, 0, 0]))
    link = ET.SubElement(model, "link", name="body")
    for kind in ("collision", "visual"):
        part = ET.SubElement(link, kind, name=kind)
        ET.SubElement(ET.SubElement(ET.SubElement(part, "geometry"), "box"), "size").text = " ".join(map(str, size))
        if kind == "visual":
            material = ET.SubElement(part, "material")
            for tag in ("ambient", "diffuse"):
                ET.SubElement(material, tag).text = " ".join(map(str, [*colour, 1]))
    return model


def _fixtures(world, x, y):
    _box(world, "task_table", (x, y, .48), (.8, .9, .96), (.48, .31, .18))
    # Dynamic cylinder, 80 g, with an independently simulated pose and friction.
    world.append(ET.fromstring(f'''<model name="red_cup"><pose>{x-.12} {y-.18} 1.025 0 0 0</pose>
      <link name="cup"><inertial><mass>0.08</mass><inertia><ixx>0.000096</ixx><iyy>0.000096</iyy><izz>0.000081</izz></inertia></inertial>
        <collision name="cup"><geometry><cylinder><radius>0.045</radius><length>0.12</length></cylinder></geometry>
          <surface><friction><ode><mu>1</mu><mu2>1</mu2></ode></friction></surface></collision>
        <visual name="cup"><geometry><cylinder><radius>0.045</radius><length>0.12</length></cylinder></geometry>
          <material><ambient>0.8 0.04 0.04 1</ambient><diffuse>0.8 0.04 0.04 1</diffuse></material></visual>
      </link></model>'''))
    _box(world, "tray_floor", (x-.12, y+.17, .978), (.18, .19, .03), (.08, .65, .7))
    for name, dx, dy, sx, sy in (("left", -.09, 0, .01, .2), ("right", .09, 0, .01, .2),
                                ("front", 0, -.1, .18, .01), ("back", 0, .1, .18, .01)):
        _box(world, "tray_"+name, (x-.12+dx, y+.17+dy, 1.015), (sx, sy, .07), (.08, .65, .7))


def compose_scene(scene: str, base_world: Path, output: Path, *, furnished_world: Path | None = None) -> Path:
    if scene not in SCENES:
        raise ValueError(f"unknown Gazebo scene: {scene}")
    if scene == "home":
        return base_world
    if scene == "home_furnished":
        if furnished_world is None or not furnished_world.is_file():
            raise ValueError("FURNISHED_WORLD_MISSING: run scripts/prepare_home_world.py")
        tree = ET.parse(furnished_world)
        world = tree.getroot().find("world")
        old = world.find("model[@name='tangying_robot']")
        pose = old.findtext("pose")
        world.remove(old)
        robot = ET.parse(base_world).getroot().find("world/model[@name='tangying_robot']")
        robot.find("pose").text = pose
        # The textured house uses a CPU rendering profile. CameraInfo and the
        # world revision follow the actual resolution; no pixels are upsampled
        # and no freshness threshold is relaxed to compensate for missed frames.
        for sensor in robot.findall("link/sensor"):
            image = sensor.find("camera/image")
            if image is not None:
                image.find("width").text = "256"
                image.find("height").text = "192"
        for light in world.findall("light"):
            shadows = light.find("cast_shadows")
            if shadows is not None:
                shadows.text = "false"
        world.append(robot)
    else:
        tree = ET.parse(base_world)
        world = tree.getroot().find("world")
        if scene == "tabletop":
            for model in list(world.findall("model")):
                if model.get("name") not in {"floor", "tangying_robot"}:
                    world.remove(model)
            world.find("model[@name='tangying_robot']/pose").text = "0 0 0.20 0 0 0"
            _fixtures(world, .95, 0.)
            _box(world, "backdrop", (2., 0., 1.), (.05, 3., 2.), (.7, .73, .77))
        else:
            island = world.find("model[@name='kitchen_island']")
            world.remove(island)
            _fixtures(world, 3.0, -.8)
    # Stable world name retains the ROS/Gazebo topic contract across scene changes.
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="  ")
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return output
