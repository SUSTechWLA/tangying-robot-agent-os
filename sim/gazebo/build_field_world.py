"""Generate the self-contained Gazebo Harmonic field-validation world.

The ground bodies are ideal force-driven mobile-base surrogates on a contact
plane. The aerial body is a thrust/attitude-controlled rigid body. Neither is
a commissioned vendor controller; no reinforcement learning policy is used.
"""

from __future__ import annotations

import argparse
import math
import xml.etree.ElementTree as ET
from pathlib import Path

SENSOR_POSE = (.12, .04, .20, .02, -.08, .12)
FIXTURES = (
    ("fixture-nw", (-3.5, 3.2, .7), (.7, 1.3, 1.4)),
    ("fixture-n", (.8, 3.7, 1.1), (1.6, .5, 2.2)),
    ("fixture-se", (3.6, -2.8, .55), (.6, 1.4, 1.1)),
    ("fixture-w", (-4.1, -1.3, 1.4), (.7, .6, 2.8)),
)


def _box(name: str, position: tuple, size: tuple) -> str:
    xyz = " ".join(map(str, position))
    dimensions = " ".join(map(str, size))
    return f'''<model name="{name}"><static>true</static><pose>{xyz} 0 0 0</pose>
      <link name="body"><collision name="collision"><geometry><box><size>{dimensions}</size>
      </box></geometry></collision><visual name="visual"><geometry><box><size>{dimensions}</size>
      </box></geometry><material><diffuse>.45 .5 .55 1</diffuse></material></visual></link></model>'''


def build_world(*, horizontal_samples: int = 360, vertical_samples: int = 17,
                robot_starts: dict[str, tuple[float, float, float]] | None = None) -> str:
    if (isinstance(horizontal_samples, bool) or isinstance(vertical_samples, bool)
            or not isinstance(horizontal_samples, int) or not isinstance(vertical_samples, int)
            or not 16 <= horizontal_samples <= 2048 or not 3 <= vertical_samples <= 128):
        raise ValueError("lidar sample counts are outside fixture bounds")
    starts = {"ground-a": (-2., -1., .16), "ground-b": (2., 1., .16), "uav-a": (0., -3., 1.2)}
    for key, value in (robot_starts or {}).items():
        if key not in starts or len(value) != 3:
            raise ValueError("unknown robot id or invalid xyz initial pose")
        if not all(math.isfinite(v) for v in value):
            raise ValueError("initial poses must be finite")
        if abs(value[0]) > 4.4 or abs(value[1]) > 4.4 or not 0 < value[2] < 2.9:
            raise ValueError("initial poses must be inside the room")
        if key.startswith("ground") and not .15 <= value[2] <= .25:
            raise ValueError("ground base must start on or just above the floor")
        starts[key] = value
    robots = []
    for name, position in starts.items():
        aerial = name == "uav-a"
        mass = 1.2 if aerial else 12
        inertia = ".025 .025 .04" if aerial else ".3 .4 .5"
        ixx, iyy, izz = inertia.split()
        size = ".46 .46 .14" if aerial else ".5 .4 .3"
        xyz = " ".join(map(str, position))
        sensor_pose = " ".join(map(str, SENSOR_POSE))
        robots.append(f'''<model name="{name}"><pose>{xyz} 0 0 0</pose>
          <link name="base_link"><inertial><mass>{mass}</mass><inertia>
          <ixx>{ixx}</ixx><iyy>{iyy}</iyy><izz>{izz}</izz><ixy>0</ixy><ixz>0</ixz><iyz>0</iyz>
          </inertia></inertial><collision name="body_collision"><geometry><box><size>{size}</size>
          </box></geometry><surface><friction><ode><mu>0.015</mu><mu2>0.015</mu2></ode></friction>
          </surface></collision><visual name="body_visual"><geometry><box><size>{size}</size></box>
          </geometry><material><diffuse>{'.8 .5 .2 1' if aerial else '.2 .5 .8 1'}</diffuse></material></visual>
          <sensor name="lidar" type="gpu_lidar"><pose>{sensor_pose}</pose>
            <topic>/field/{name}/lidar</topic><update_rate>10</update_rate><always_on>true</always_on>
            <lidar><scan><horizontal><samples>{horizontal_samples}</samples><resolution>1</resolution>
              <min_angle>-3.141592653589793</min_angle><max_angle>3.141592653589793</max_angle></horizontal>
              <vertical><samples>{vertical_samples}</samples><resolution>1</resolution>
              <min_angle>-.4</min_angle><max_angle>.6</max_angle></vertical></scan>
              <range><min>.05</min><max>15</max><resolution>.001</resolution></range>
            </lidar></sensor>
          <sensor name="imu" type="imu"><topic>/field/{name}/imu</topic><update_rate>100</update_rate>
            <always_on>true</always_on></sensor>
          <sensor name="contact" type="contact"><topic>/field/{name}/contacts</topic>
            <always_on>true</always_on><update_rate>100</update_rate>
            <contact><collision>body_collision</collision></contact></sensor>
          </link><plugin filename="libtangying_field_controller.so" name="tangying::FieldController">
            <aerial>{str(aerial).lower()}</aerial><mass>{mass}</mass><lease_seconds>.4</lease_seconds>
          </plugin></model>''')
    walls = (
        ("wall-east", (5.1, 0, 1.5), (.2, 10.4, 3)),
        ("wall-west", (-5.1, 0, 1.5), (.2, 10.4, 3)),
        ("wall-north", (0, 5.1, 1.5), (10, .2, 3)),
        ("wall-south", (0, -5.1, 1.5), (10, .2, 3)),
    )
    xml = f'''<?xml version="1.0"?>
    <sdf version="1.9"><world name="field_validation">
      <physics name="field-physics" type="ignored"><max_step_size>.002</max_step_size>
        <real_time_factor>1</real_time_factor></physics><gravity>0 0 -9.81</gravity>
      <plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
      <plugin filename="gz-sim-user-commands-system" name="gz::sim::systems::UserCommands"/>
      <plugin filename="gz-sim-scene-broadcaster-system" name="gz::sim::systems::SceneBroadcaster"/>
      <plugin filename="gz-sim-imu-system" name="gz::sim::systems::Imu"/>
      <plugin filename="gz-sim-contact-system" name="gz::sim::systems::Contact"/>
      <plugin filename="gz-sim-sensors-system" name="gz::sim::systems::Sensors"><render_engine>ogre2</render_engine></plugin>
      <light name="sun" type="directional"><pose>0 0 10 0 0 0</pose><diffuse>1 1 1 1</diffuse>
        <direction>-.5 .1 -1</direction></light>
      <model name="floor"><static>true</static><link name="floor_link">
        <collision name="floor_collision"><geometry><plane><normal>0 0 1</normal><size>20 20</size>
        </plane></geometry></collision><visual name="floor_visual"><geometry><plane><normal>0 0 1</normal>
        <size>20 20</size></plane></geometry><material><diffuse>.7 .7 .7 1</diffuse></material></visual>
      </link></model>{''.join(_box(*item) for item in (*walls, *FIXTURES))}{''.join(robots)}
    </world></sdf>'''
    root = ET.fromstring(xml)
    ET.indent(root, space="  ")
    return '<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(build_world(), encoding="utf-8")


if __name__ == "__main__":
    main()
