#!/usr/bin/env python3
"""Freeze the simulator's arm chain into a table the gateway can do FK with.

    scripts/generate_arm_kinematics.py             # rewrite the checked-in asset
    scripts/generate_arm_kinematics.py --check     # fail if the asset is stale

Hand-eye calibration needs ``A``: how the link under a camera moved between two
views. That is forward kinematics, and the only geometry this repository has is
MuJoCo's XLeRobot model. Rather than make the gateway import MuJoCo *and* the
simulation package to reach it (the robot-side gateway ships without the scene
XML, and a live model handed to the robot half would be a second source of truth
for the same link lengths), the chain is read out of the model once, here, and
checked in next to the module that consumes it.

The table is not a hand-written DH list and must not become one: everything in it
is copied from the model, and ``sim/mujoco/tests/test_arm_kinematics_ground_truth.py``
re-derives it from the live model and compares. Edit the XML, and that test points
at this script.

Chain order is not invented either. Starting at ``chassis`` the walk follows the
unique child body whose own hinge joint name ends in ``_L`` (or ``_R``), which is
the kinematic chain of that arm and nothing else. The joints it yields are, in
order, the six a person moves along one arm — so index *i* of the chain is servo
id *i* of that arm's bus, which is how ``ARM_JOINTS`` defines the motor names.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import mujoco

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot/gateway"))
sys.path.insert(0, str(ROOT / "sim/mujoco"))

from tangying_robot_gateway.calibration import ARM_JOINTS

SCHEMA_VERSION = "robot.arm_kinematics.v1"
ASSET_PATH = (ROOT / "robot/gateway/tangying_robot_gateway/assets" / "arm_kinematics.json")

#: The link every arm hangs off. Not the world: an arm's motion relative to its own
#: base is what hand-eye needs, and the base's pose in the world is a property of
#: where the robot was put, not of the robot.
BASE_BODY = "chassis"

#: Suffix on the model's joint names that names the arm. The upstream model spells
#: the left arm's joints ``Rotation_L``…``Jaw_L`` and the right arm's ``…_R``.
SIDE_SUFFIX = {"left": "_L", "right": "_R"}

#: How many links one arm has. Six joints, six driven bodies: the last one is the
#: moving jaw, which is what ``gripper`` drives.
LINKS_PER_ARM = 6


class ChainError(RuntimeError):
    """The model does not contain the arm chain this script is supposed to freeze."""


def _hinge_joint_of(model: mujoco.MjModel, body_id: int) -> int:
    """The single hinge joint a body owns, or -1 when it owns none/more than one."""
    joints = [jid for jid in range(model.njnt) if int(model.jnt_bodyid[jid]) == body_id]
    hinges = [jid for jid in joints if int(model.jnt_type[jid]) == int(mujoco.mjtJoint.mjJNT_HINGE)]
    if len(joints) != len(hinges) or len(hinges) > 1:
        # A free joint, a slide joint or a second hinge on the same body means the
        # walk below would silently pick one of several parents. Refuse instead.
        raise ChainError(
            f"body {mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id)!r} "
            f"owns {len(joints)} joints ({len(hinges)} hinge); expected exactly one hinge")
    return hinges[0] if hinges else -1


def _children(model: mujoco.MjModel, body_id: int) -> list[int]:
    return [bid for bid in range(model.nbody) if int(model.body_parentid[bid]) == body_id]


def arm_chain_from_model(
    model: mujoco.MjModel,
) -> dict[str, tuple[int, ...]]:
    """The body id of each arm's link, in chain order, straight from the model."""
    chassis = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, BASE_BODY)
    if chassis < 0:
        raise ChainError(f"the model has no body named {BASE_BODY!r}")

    chains: dict[str, tuple[int, ...]] = {}
    for side, suffix in SIDE_SUFFIX.items():
        body = chassis
        links: list[int] = []
        while True:
            following = [
                child for child in _children(model, body)
                if (joint := _hinge_joint_of(model, child)) >= 0
                and (mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint) or "").endswith(suffix)
            ]
            if not following:
                break
            if len(following) != 1:
                raise ChainError(
                    f"{body!r} has {len(following)} child bodies whose joint ends in {suffix!r}; "
                    "the chain is not a chain")
            body = following[0]
            links.append(body)
        if len(links) != LINKS_PER_ARM:
            raise ChainError(
                f"the {side} arm has {len(links)} links in the model, not {LINKS_PER_ARM}")
        chains[side] = tuple(links)
    return chains


def build_table(model: mujoco.MjModel, *, scene: str, model_path: Path,
                mjcf_path: Path, model_revision: str) -> dict:
    """The whole checked-in table, read out of a loaded model."""
    chains = arm_chain_from_model(model)
    chassis = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, BASE_BODY)
    arms: dict[str, list[dict]] = {}
    for side, bodies in chains.items():
        entries = []
        for index, body_id in enumerate(bodies, start=1):
            joint = _hinge_joint_of(model, body_id)
            joint_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint)
            body_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id)
            parent_name = mujoco.mj_id2name(
                model, mujoco.mjtObj.mjOBJ_BODY, int(model.body_parentid[body_id]))
            entries.append({
                "index": index,
                "link": f"{side}_arm_link{index}",
                # The motor this joint is driven by. Both sides reuse servo ids 1..6
                # on separate buses, so the *position* in the chain is the id.
                "motor": f"{side}_arm_{ARM_JOINTS[index - 1]}",
                "joint": ARM_JOINTS[index - 1],
                "body": body_name,
                "parentBody": parent_name,
                "jointName": joint_name,
                "position": [float(value) for value in model.body_pos[body_id]],
                "quaternion": [float(value) for value in model.body_quat[body_id]],
                "axis": [float(value) for value in model.jnt_axis[joint]],
                "anchor": [float(value) for value in model.jnt_pos[joint]],
                "rangeMin": float(model.jnt_range[joint][0]),
                "rangeMax": float(model.jnt_range[joint][1]),
            })
        arms[side] = entries
    return {
        "schemaVersion": SCHEMA_VERSION,
        "provenance": {
            "scene": scene,
            "modelPath": str(model_path.relative_to(ROOT)) if model_path.is_absolute() else str(model_path),
            "mjcf": str(mjcf_path.relative_to(ROOT)) if mjcf_path.is_absolute() else str(mjcf_path),
            "modelRevision": model_revision,
            "generator": "scripts/generate_arm_kinematics.py",
            "note": ("Every number here is copied from the MuJoCo model; the table is a "
                     "cache of that model, never a second definition of it. Regenerate "
                     "with scripts/generate_arm_kinematics.py after editing the XML."),
        },
        "base": {
            "body": BASE_BODY,
            "position": [float(value) for value in model.body_pos[chassis]],
            "quaternion": [float(value) for value in model.body_quat[chassis]],
        },
        "arms": arms,
    }


def load_model(scene: str) -> tuple[mujoco.MjModel, Path, Path]:
    """The same model the runtime commissions, and the scene file it came from."""
    from tangying_sim.home_scene import HOME_MODEL_PATH
    from tangying_sim.model import TASK_MODEL_PATH
    from tangying_sim.rgbd_navigation import load_navigation_model

    model_path = HOME_MODEL_PATH if scene in {"home", "home_task"} else TASK_MODEL_PATH
    return (load_navigation_model(scene=scene), model_path,
            Path("sim/mujoco/assets/xlerobot/xlerobot.xml"))


def serialize(table: dict) -> str:
    return json.dumps(table, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", default="home_task",
                        help="navigation scene whose model the chain is read from")
    parser.add_argument("--check", action="store_true",
                        help="do not write; fail when the asset differs from the model")
    arguments = parser.parse_args()

    from tangying_sim.model import MODEL_REVISION

    model, model_path, mjcf_path = load_model(arguments.scene)
    table = build_table(model, scene=arguments.scene, model_path=model_path,
                        mjcf_path=mjcf_path, model_revision=MODEL_REVISION)
    text = serialize(table)
    if arguments.check:
        current = ASSET_PATH.read_text(encoding="utf-8") if ASSET_PATH.is_file() else ""
        if current != text:
            print(f"{ASSET_PATH} is stale; run scripts/generate_arm_kinematics.py",
                  file=sys.stderr)
            return 1
        print(f"{ASSET_PATH} matches the model")
        return 0
    ASSET_PATH.parent.mkdir(parents=True, exist_ok=True)
    ASSET_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {ASSET_PATH} ({len(text)} bytes)")
    for side, entries in table["arms"].items():
        print(f"  {side}: " + " -> ".join(
            f"{entry['link']}({entry['jointName']} motor={entry['motor']})" for entry in entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
