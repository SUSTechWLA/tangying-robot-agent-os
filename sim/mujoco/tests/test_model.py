import mujoco
from tangying_sim.model import MODEL_REVISION, load_task_model, validate_task_model


def test_task_model_contains_pinned_xlerobot_and_task_scene():
    model = load_task_model()
    validate_task_model(model)

    assert MODEL_REVISION == "3d14695e40c9c68229c0aacffca6053c75cd3eb6"
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "chassis") >= 0
    for name in ("Rotation_L", "Rotation_R", "Jaw_L", "Jaw_R"):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) >= 0
    for name in ("red_cup", "blue_bottle", "right_bin", "front_tray"):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name) >= 0
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "overview") >= 0
