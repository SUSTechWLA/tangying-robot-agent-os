from pathlib import Path

import mujoco

MODEL_REVISION = "3d14695e40c9c68229c0aacffca6053c75cd3eb6"
TASK_MODEL_PATH = Path(__file__).resolve().parents[1] / "assets" / "xlerobot_tabletop.xml"
REQUIRED_BODIES = (
    "chassis",
    "Fixed_Jaw",
    "Fixed_Jaw_2",
    "red_cup",
    "blue_bottle",
    "right_bin",
    "front_tray",
)
REQUIRED_JOINTS = (
    "Rotation_L",
    "Pitch_L",
    "Elbow_L",
    "Jaw_L",
    "Rotation_R",
    "Pitch_R",
    "Elbow_R",
    "Jaw_R",
)


def load_task_model() -> mujoco.MjModel:
    return mujoco.MjModel.from_xml_path(str(TASK_MODEL_PATH))


def validate_task_model(model: mujoco.MjModel) -> None:
    required_names = (
        (mujoco.mjtObj.mjOBJ_BODY, REQUIRED_BODIES),
        (mujoco.mjtObj.mjOBJ_JOINT, REQUIRED_JOINTS),
    )
    for kind, names in required_names:
        missing = [name for name in names if mujoco.mj_name2id(model, kind, name) < 0]
        if missing:
            raise ValueError(f"XLeRobot task model is missing {missing}")
