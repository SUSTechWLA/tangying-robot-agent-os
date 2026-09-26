"""Resolve the sole commissioned Gazebo home; aliases share the same prototype.

The checked-in SDF is an inspectable generated snapshot. The running SDF and CAD
resources are rebuilt by export_gazebo_home.py and bound by their manifest.
Historical coloured workcell geometry is confined to automated test fixtures.
"""
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

SCENES = ('home', 'home_task', 'home_furnished')


def compose_scene(scene: str, base_world: Path, output: Path, *, furnished_world: Path | None = None) -> Path:
    if scene not in SCENES:
        raise ValueError('Gazebo supports the XLeRobot furnished home; tabletop was retired. Use MuJoCo for historical tabletop tests.')
    source = furnished_world or base_world
    if not source.is_file():
        raise ValueError('HOME_RESOURCE_MISSING: run scripts/export_gazebo_home.py')
    world = ET.parse(source).getroot().find('world')
    robot = world.find("model[@name='tangying_robot']") if world is not None else None
    if (robot is None or robot.find("joint[@name='base_slide_x']") is None
            or robot.find("joint[@name='base_slide_y']") is None
            or robot.find("joint[@name='base_yaw']") is None
            or any(world.find(f"model[@name='{name}']") is None
                   for name in ('living_room','kitchen','bedroom','bathroom','ceramic_mug','kitchen_tray'))):
        raise ValueError('HOME_PROTOTYPE_MISMATCH: expected the commissioned XLeRobot household')
    output.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != output.resolve(): shutil.copyfile(source, output)
    return output
