"""Gazebo scene composition. Geometry initializes physics; it is never perception.

Each named scene has a distinct SDF hash and map namespace. The task fixtures are
commissioning geometry, not a claim that grasp controllers are already validated.
"""
from __future__ import annotations

import random
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
    # The chassis can approach below the worktop; a solid floor-to-top box
    # would make every reachable tabletop pose collide with the mobile base.
    _box(world, "task_table", (x+.51, y-.12, .92), (.66, .9, .04), (.48, .31, .18))
    for dy in (-.51, .27):
        _box(world, "table_leg_"+str(dy), (x+.8, y+dy, .45), (.04, .04, .9), (.3, .3, .3))
    # Dynamic cylinder, 80 g, with an independently simulated pose and friction.
    world.append(ET.fromstring(f'''<model name="red_cup"><pose>{x+.44} {y-.24} 1.005 0 0 0</pose>
      <link name="cup"><inertial><mass>0.08</mass><inertia><ixx>0.000096</ixx><iyy>0.000096</iyy><izz>0.000081</izz></inertia></inertial>
        <collision name="cup"><geometry><cylinder><radius>0.045</radius><length>0.12</length></cylinder></geometry>
          <surface><friction><ode><mu>1</mu><mu2>1</mu2></ode></friction></surface></collision>
        <visual name="cup"><geometry><cylinder><radius>0.045</radius><length>0.12</length></cylinder></geometry>
          <material><ambient>0.8 0.04 0.04 1</ambient><diffuse>0.8 0.04 0.04 1</diffuse></material></visual>
      </link></model>'''))
    bottle = ET.fromstring(ET.tostring(world.find("model[@name='red_cup']")))
    bottle.set("name", "blue_bottle")
    bottle.find("pose").text = f"{x+.48} {y-.05} 1.005 0 0 0"
    for colour in bottle.findall("link/visual/material/*"):
        colour.text = "0.04 0.08 0.85 1"
    world.append(bottle)
    _box(world, "tray_floor", (x+.32, y+.03, .955), (.18, .19, .03), (.08, .65, .7))
    _box(world, "delivery_tray", (x+.30, y-.36, .955), (.18, .19, .03), (.8, .1, .7))
    for name, dx, dy, sx, sy in (("left", -.09, 0, .01, .2), ("right", .09, 0, .01, .2),
                                ("front", 0, -.1, .18, .01), ("back", 0, .1, .18, .01)):
        _box(world, "tray_"+name, (x+.32+dx, y+.03+dy, .99), (sx, sy, .04), (.08, .65, .7))


def _floor_pattern(world, x, y):
    # A visible commissioning mat gives RGB-D SLAM observable image features.
    # It contains no poses/labels for the robot and has no collision surface.
    # Plain tabletop scenery produced only 15 vocabulary words (20 required).
    model = ET.SubElement(world, "model", name="workcell_feature_mat")
    ET.SubElement(model, "static").text = "true"
    link = ET.SubElement(model, "link", name="mat")
    rng = random.Random(7321)
    for i in range(18):
        for j in range(24):
            if rng.random() < .45:
                continue
            part = ET.SubElement(link, "visual", name=f"tile_{i}_{j}")
            ET.SubElement(part, "pose").text = f"{x+.6+i*.1} {y-1.2+j*.1} .002 0 0 0"
            ET.SubElement(ET.SubElement(ET.SubElement(part, "geometry"), "box"), "size").text = ".07 .07 .001"
            material = ET.SubElement(part, "material")
            colour = .08 if rng.random() < .6 else .88
            for tag in ("ambient", "diffuse"):
                ET.SubElement(material, tag).text = f"{colour} {colour} {colour} 1"


def compose_scene(scene: str, base_world: Path, output: Path, *, furnished_world: Path | None = None) -> Path:
    if scene not in SCENES:
        raise ValueError(f"unknown Gazebo scene: {scene}")
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
            _box(world, "backdrop", (2., 0., 1.), (.05, 3., 2.), (.7, .73, .77))
        elif scene == "home_task":
            island = world.find("model[@name='kitchen_island']")
            world.remove(island)
    robot = world.find("model[@name='tangying_robot']")
    if world.find("plugin[@name='gz::sim::systems::Imu']") is None:
        ET.SubElement(world, "plugin", filename="gz-sim-imu-system", name="gz::sim::systems::Imu")
    x, y = map(float, robot.findtext("pose").split()[:2])
    _fixtures(world, x, y)
    _floor_pattern(world, x, y)
    ET.SubElement(robot, "plugin", filename="libtangying_suction.so", name="tangying::Suction")
    # Stable world name retains the ROS/Gazebo topic contract across scene changes.
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="  ")
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return output
