"""Generate the Gazebo arm chain from the same table the forward kinematics uses.

Why generate rather than hand-write
-----------------------------------
`robot/gateway/tangying_robot_gateway/assets/arm_kinematics.json` is the arm's
kinematic chain as the *gateway* knows it, exported from the MuJoCo model and
verified against it to 1e-16 (`sim/mujoco/tests/test_arm_kinematics_ground_truth.py`).
A second simulator with a hand-written copy of the same robot would drift from
that table the first time anyone adjusted a link, and the drift would show up as
"the arm reaches in Gazebo but not on the robot" - the most expensive kind of
difference, because both sides look right on their own.

So Gazebo's arms are generated from the table, with link geometry derived from the
parent-to-child offsets the table already carries. The geometry is deliberately
simple - boxes and cylinders rather than the CAD meshes - because this backend
exists to exercise the *system* end to end, not to train against. The moving jaw carries collision geometry and friction. Physical grasping still
requires commissioned fixed-jaw geometry and a validated contact controller;
joint-motion acceptance alone does not establish grasp capability.

Chain order, joint axes, joint limits and the motor each joint answers to all come
from the table unchanged, so `left_arm_shoulder_lift` means the same joint, with
the same range, in either simulator.

    python scripts/generate_gazebo_arm.py            # splice into the world
    python scripts/generate_gazebo_arm.py --check     # fail if the world is stale
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TABLE = ROOT / "robot/gateway/tangying_robot_gateway/assets/arm_kinematics.json"
WORLD = ROOT / "robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf"

BEGIN = "<!-- BEGIN generated arms: scripts/generate_gazebo_arm.py -->"
END = "<!-- END generated arms -->"

#: Half-thickness of a link, in metres. The table carries positions, not sizes, so
#: every link is a capsule of this radius. Big enough that two links cannot pass
#: through each other, small enough that the arm still fits through a doorway.
LINK_RADIUS_M = 0.022
#: The jaw pads are thinner and wider than the arm, so a grasp has a face rather
#: than a point.
JAW_HALF_M = (0.016, 0.020, 0.006)
#: Coulomb friction on the jaws. Grasping is friction, so a low value here is a
#: gripper that closes and drops things - which would look like a planner fault.
JAW_FRICTION = 1.2
#: Mass per link. Sums to about 1.6 kg per arm, near the reference unit's.
LINK_MASS_KG = 0.26
#: What the table calls the chassis and what this world calls it.
#:
#: A translation at the seam, written down rather than assumed equal. Gazebo
#: refused the first generated world with "parent frame with name[chassis] not
#: found in model[tangying_robot]", which is the right complaint: the table names
#: bodies the way MuJoCo does, and the world names its base link its own way. The
#: table stays the authority on the chain; this is the one place the two names
#: meet.
BASE_LINK_IN_WORLD = "base_link"


def quaternion_to_rpy(q: list[float]) -> tuple[float, float, float]:
    """SDF poses are x y z roll pitch yaw, and the table stores quaternions."""
    w, x, y, z = (float(v) for v in q)
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


def numbers(values) -> str:
    return " ".join(f"{float(v):.6g}" for v in values)


def link_pose(link: dict) -> str:
    roll, pitch, yaw = quaternion_to_rpy(link["quaternion"])
    return f"{numbers(link['position'])} {numbers((roll, pitch, yaw))}"


def child_offset(arms: dict, side: str, index: int) -> tuple[float, float, float]:
    """Where the next link sits in this link's frame, which is how long it is.

    The table gives each body's position relative to its parent, so the distance
    to the child is the only length information it carries. Deriving geometry from
    it keeps the drawing honest to the kinematics: a link drawn longer than its
    child offset would collide with its own neighbour at the joint limit.
    """
    chain = arms[side]
    if index < len(chain) - 1:
        nxt = chain[index + 1]["position"]
        return (float(nxt[0]), float(nxt[1]), float(nxt[2]))
    # The last link - the moving jaw - has no child, so it gets the pad size.
    return (0.0, 0.0, 0.02)


def generate_link(side: str, link: dict, offset: tuple[float, float, float],
                  *, is_jaw: bool, parent_link: str) -> str:
    name = link["link"]
    if is_jaw:
        geometry = (f'<box><size>{2 * JAW_HALF_M[0]:.6g} {2 * JAW_HALF_M[1]:.6g} '
                    f'{2 * JAW_HALF_M[2]:.6g}</size></box>')
        mass = 0.08
        friction = (f'<surface><friction><ode><mu>{JAW_FRICTION}</mu>'
                    f'<mu2>{JAW_FRICTION}</mu2></ode></friction></surface>')
    else:
        length = max(math.dist((0, 0, 0), offset), 0.03)
        geometry = (f'<cylinder><radius>{LINK_RADIUS_M:.6g}</radius>'
                    f'<length>{length:.6g}</length></cylinder>')
        mass = LINK_MASS_KG
        friction = ""
    inertia = mass * 1e-4
    return (
        f'      <link name="{name}">\n'
        f'        <pose relative_to="{parent_link}">{link_pose(link)}</pose>\n'
        f'        <inertial><mass>{mass:.6g}</mass>'
        f'<inertia><ixx>{inertia:.6g}</ixx><iyy>{inertia:.6g}</iyy>'
        f'<izz>{inertia:.6g}</izz><ixy>0</ixy><ixz>0</ixz><iyz>0</iyz></inertia></inertial>\n'
        f'        <collision name="c"><geometry>{geometry}</geometry>{friction}</collision>\n'
        f'        <visual name="v"><geometry>{geometry}</geometry>'
        f'<material><ambient>0.84 0.62 0.24 1</ambient>'
        f'<diffuse>0.84 0.62 0.24 1</diffuse></material></visual>\n'
        f'      </link>\n'
    )


def generate_joint(link: dict, parent_link: str, *, is_jaw: bool) -> str:
    """One revolute joint, named after the motor it answers to.

    The joint *name* is the motor name from the calibration document
    (`left_arm_shoulder_lift`), not the MuJoCo body name. A runtime bridge maps a
    skill onto a motor, and that mapping is the interface the rest of the system
    already speaks; naming the joint anything else would put a translation layer
    in the one place that must not have one.
    """
    damping = 2.0 if is_jaw else 0.6
    return (
        f'      <joint name="{link["motor"]}" type="revolute">\n'
        f'        <parent>{parent_link}</parent><child>{link["link"]}</child>\n'
        f'        <axis><xyz>{numbers(link["axis"])}</xyz>'
        f'<limit><lower>{float(link["rangeMin"]):.6g}</lower>'
        f'<upper>{float(link["rangeMax"]):.6g}</upper>'
        f'<effort>{40 if is_jaw else 20}</effort><velocity>3.0</velocity></limit>'
        f'<dynamics><damping>{damping}</damping></dynamics></axis>\n'
        f'      </joint>\n'
    )


def generate_controllers(table: dict) -> str:
    """One position controller per arm joint, after the links they drive.

    Emitted from the same table as the links, so a joint that exists always has a
    controller and a controller never names a joint that does not. Without these
    the arms are geometry: Gazebo will happily simulate them falling under gravity
    and ignore every command, which looks like a bridge fault rather than a
    missing plugin.

    The gripper gets a stiffer controller than the arm. A jaw that sags under a
    light load is a grasp that opens by itself, and that reads as a planner
    failure several layers up.
    """
    parts = ["      <!-- Generated joint controllers: same table, same order. -->\n"]
    for side in sorted(table["arms"]):
        for link in table["arms"][side]:
            is_jaw = link is table["arms"][side][-1]
            parts.append(
                f'      <plugin filename="gz-sim-joint-position-controller-system" '
                f'name="gz::sim::systems::JointPositionController">\n'
                f'        <joint_name>{link["motor"]}</joint_name>\n'
                f'        <topic>/joint/{link["motor"]}/cmd_pos</topic>\n'
                '        <p_gain>120</p_gain>\n'
                f'        <i_gain>{1.0 if is_jaw else 4.0}</i_gain>\n'
                f'        <d_gain>{0.5 if is_jaw else 0.1}</d_gain>\n'
                f'        <i_max>{2.0 if is_jaw else 10.0}</i_max>\n'
                f'        <i_min>{-2.0 if is_jaw else -10.0}</i_min>\n'
                f'        <cmd_max>{40 if is_jaw else 20}</cmd_max>\n'
                f'        <cmd_min>{-40 if is_jaw else -20}</cmd_min>\n'
                f'      </plugin>\n')
    return "".join(parts)


def generate(table: dict) -> str:
    arms = table["arms"]
    parts = [BEGIN + "\n",
             ("      <!-- Generated from arm_kinematics.json. Edit the table, not this.\n"
             "           Geometry is simplified on purpose; joint axes, limits and motor\n"
             "           names are the table's, so both simulators describe one robot. -->\n")]
    for side in sorted(arms):
        chain = arms[side]
        # The first link hangs off the chassis: it is the only link whose parent is
        # not part of this chain, and the only name that needs translating.
        parent = BASE_LINK_IN_WORLD
        for index, link in enumerate(chain):
            is_jaw = index == len(chain) - 1
            parts.append(generate_link(side, link, child_offset(arms, side, index),
                                       is_jaw=is_jaw, parent_link=parent))
            parts.append(generate_joint(link, parent, is_jaw=is_jaw))
            parent = link["link"]
    parts.append(generate_controllers(table))
    parts.append('      <plugin filename="gz-sim-joint-state-publisher-system" name="gz::sim::systems::JointStatePublisher">'
                 '<topic>/joint_states</topic><update_rate>30</update_rate></plugin>\n')
    parts.append("      " + END + "\n")
    return "".join(parts)


def splice(world: str, generated: str) -> str:
    start = world.find(BEGIN)
    end = world.find(END)
    if start < 0 or end < 0:
        raise SystemExit(
            f"{WORLD} has no generated-arms markers ({BEGIN!r} .. {END!r}). "
            "Add them inside the robot model, then re-run.")
    if end < start:
        raise SystemExit("the generated-arms markers are in the wrong order")
    # Replace the whole marked region, markers included. The markers come back as
    # part of `generated`: dropping them would make the next run refuse to splice
    # and --check unable to notice a stale world, which is the failure this whole
    # mechanism exists to prevent.
    line_start = world.rfind("\n", 0, start) + 1
    indent = world[line_start:start]
    after = world.find("\n", end + len(END))
    after = len(world) if after < 0 else after + 1
    indented = "".join((indent + line) if line.strip() else line
                       for line in generated.splitlines(True))
    return world[:line_start] + indented + world[after:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero when the world does not match the table")
    args = parser.parse_args()
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    generated = generate(table)
    world = WORLD.read_text(encoding="utf-8")
    updated = splice(world, generated)
    if args.check:
        if updated != world:
            print(f"{WORLD} is stale; run scripts/generate_gazebo_arm.py", file=sys.stderr)
            return 1
        print(f"{WORLD} matches {TABLE.name}")
        return 0
    if updated == world:
        print(f"{WORLD} already up to date")
        return 0
    WORLD.write_text(updated, encoding="utf-8")
    links = sum(len(chain) for chain in table["arms"].values())
    print(f"{WORLD}: {links} links in {len(table['arms'])} arms regenerated from "
          f"{TABLE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
