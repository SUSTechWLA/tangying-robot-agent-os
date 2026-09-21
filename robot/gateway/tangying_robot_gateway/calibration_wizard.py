"""Guided calibration for someone who has never calibrated a robot.

The contract in :mod:`tangying_robot_gateway.calibration` says *what* a
calibration is. This module says *how a person produces one*: a short, ordered
list of things to physically do, each written in plain language, each recording
exactly one measurement, resumable at any point.

Design rules, all driven by "a non-technical owner is holding the robot":

* **One action per step.** Never "set the offsets"; always "turn this joint to the
  position in the picture, then press Enter".
* **The machine says which joint, not the user.** Every step names the arm, the
  joint and the physical bus, so nobody has to map ``left_arm_elbow_flex`` onto a
  piece of metal.
* **Nothing is recorded that was not measured.** A step reads the servo; it never
  invents a value, and a failed read leaves the step unfinished rather than
  writing a plausible number.
* **Stopping is normal.** State is written after every step, so a session can be
  closed and resumed, and the summary always says how much is left.
* **Travel limits are sampled per arm, not per joint**, because sweeping one arm
  through its range while the wizard watches is two minutes of work instead of
  thirty-two confirmations.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from tangying_robot_gateway.calibration import (
    ARM_JOINTS,
    ARM_SIDES,
    GRIPPER_JOINT,
    MOTOR_BUS,
    MOTOR_IDS,
    CalibrationError,
    validate_calibration,
)

SESSION_SCHEMA = "robot.calibration.session.v1"

ARM_LABEL = {"left": "左臂", "right": "右臂", "shared": "头部与底盘"}

#: What each joint is called when talking to a person, plus how to pose it for the
#: zero capture. The wording assumes the arm is powered down and back-drivable.
JOINT_GUIDE = {
    "shoulder_pan": ("肩部旋转", "把这一节转到正对机器人正前方的位置"),
    "shoulder_lift": ("肩部抬升", "把大臂抬到与地面平行的高度"),
    "elbow_flex": ("肘部", "把小臂伸直，与地面平行"),
    "wrist_flex": ("腕部俯仰", "让手腕保持水平，夹爪朝前"),
    "wrist_roll": ("腕部旋转", "让夹爪的两个指头上下相对（不要左右相对）"),
    GRIPPER_JOINT: ("夹爪", "让两个指头刚好轻轻接触（中间不留缝，也不要夹紧）"),
    "head_motor_1": ("头部旋转", "把头部转到正对机器人正前方"),
    "head_motor_2": ("头部俯仰", "让头部相机水平看向前方"),
    "base_left_wheel": ("左驱动轮", "把机器人架起来或放在地上，让轮子能自由转动"),
    "base_right_wheel": ("右驱动轮", "把机器人架起来或放在地上，让轮子能自由转动"),
}

#: What has to be physically connected before anything can be measured, written
#: as the things a person has to do rather than as the ports they are on. The
#: console re-reads the robot's own report after each one, so "connected" is a
#: measurement and not a checkbox.
CONNECTION_STEPS = (
    ("power", "接通机器人电源", "先接电源，再插数据线：带电插拔串口是这类舵机最常见的损坏原因。",
     "power", "机器人上电后，控制台会在“连接机器人”里看到它。"),
    ("usb", "插入控制板数据线", "把控制板（通常是 USB-C 或 micro-USB）接到这台电脑。",
     "usb", "系统会自动扫描串口与舵机总线，不需要手填端口号。"),
    ("head_camera", "插入头部 RGB-D 相机", "把头部相机接到同一台电脑的 USB 3.0 口。",
     "camera", "图像出现在“机器人视野”里，说明这一路通了。"),
    ("base_camera", "插入底盘 RGB-D 相机", "把底盘相机接到另一个 USB 3.0 口；两个相机不要共用一个控制器。",
     "camera", "两路画面都能看到时，这一项才算完成。"),
)

#: How many board views the intrinsics step asks for. Twelve is the smallest count
#: that constrains focal length, principal point and distortion without asking an
#: operator to stand there for twenty minutes; it is a parameter of the request,
#: not a property of the solver, so a deployment may ask for more.
INTRINSICS_VIEWS = 12

#: How many arm poses the hand-eye step asks for. Eight spanning poses is the
#: usual floor for AX=XB to be determined; fewer and the rotation part is only
#: constrained in the directions the operator happened to move.
HANDEYE_POSES = 8

PREFLIGHT_CHECKS = (
    ("hardware_connected", "机器人已通电，USB 线已插好"),
    ("area_clear", "机械臂周围一个手臂范围内没有人和杂物"),
    ("estop_reachable", "实体急停开关在手边，随时可以按下"),
    ("arm_backdrivable", "机械臂处于可以自由用手转动的状态（未上电锁死）"),
)


class CalibrationHardware(Protocol):
    """The narrow slice of a robot the wizard needs.

    Deliberately small: real hardware and the simulated backend implement the same
    four calls, so the whole guided flow can be exercised without a robot.
    """

    def describe(self) -> dict[str, Any]:
        """Human-readable facts about the connected unit (ports, ids, firmware)."""

    def blocking_problems(self) -> list[str]:
        """Plain-language reasons calibration cannot start; empty means ready."""

    def read_motor_position(self, motor: str) -> int:
        """Current raw encoder count of one servo."""

    def close(self) -> None:
        """Release the bus."""


class BoardCaptureHardware(Protocol):
    """The optional capability the camera steps need: one frame, and what was in it.

    Deliberately *not* part of :class:`CalibrationHardware`. A backend that can
    read servos but cannot look through a camera is still a perfectly good backend
    for the motor half of the flow; folding this in would make every such backend
    fail on the first line of the first step instead of at the one step that
    genuinely needs eyes.
    """

    def capture_calibration_view(self, camera: str) -> dict[str, Any] | None:
        """What ``camera`` sees right now, or ``None`` when no board was recognised.

        The wizard does not interpret a frame. Whatever finds the board — the
        gateway's own pipeline, a vendor SDK, or a person clicking four corners —
        hands back flat JSON-safe fields, and the wizard only counts the views,
        keeps them, and gives them to a solver.
        """


class CalibrationSolver(Protocol):
    """Turns the kept board views into the numbers a calibration document holds.

    A separate object because the two halves have different lifetimes and
    different owners: capturing is tied to the robot on the bench, solving is
    arithmetic on what was captured, and it may be redone later — with a better
    estimator, or on another machine — from the same session file.
    """

    def solve_intrinsics(self, camera: str, views: list[dict[str, Any]]) -> dict[str, Any]:
        """Focal length, principal point and distortion for one camera."""

    def solve_handeye(self, views: list[dict[str, Any]], *,
                      intrinsics: Mapping[str, Any] | None = None,
                      motors: Mapping[str, Mapping[str, Any]] | None = None,
                      parent_link: Any = None) -> dict[str, Any]:
        """Where the camera sits relative to the link it is bolted to.

        The three keyword arguments are what a hand-eye solve needs *besides* the
        views, and none of them can be recovered from the views themselves: the lens
        already measured for this camera (without it no board pose can be recovered
        from pixels), the motor half of the document in force (a servo count is not an
        angle without its zero), and the link the answer is expressed in. A solver
        that is not given one of them must refuse and name it rather than assume it.
        """


def _leaf_fields(payload: Any, *, code: str) -> dict[str, Any]:
    """Copy a backend's answer down to flat JSON-safe leaves, or refuse it.

    A capture backend is free to hand back whatever it likes — a dataclass, a
    frame handle, a vendor object. The session file may only contain numbers,
    strings, booleans and null, and a wizard that wrote an opaque object into it
    would produce a session that cannot be re-read after a restart. Refusing is
    better than a session that fails to load tomorrow.
    """
    if not isinstance(payload, dict) or not payload:
        raise CalibrationError(code, "相机返回的观测不是一组可记录的字段")

    def recordable(value: Any) -> bool:
        return value is None or isinstance(value, (int, float, str, bool))

    leaves: dict[str, Any] = {}
    for key in payload:
        value = payload[key]
        if isinstance(value, (list, tuple)):
            if not all(recordable(item) for item in value):
                raise CalibrationError(code, f"观测字段 {key} 含无法记录的元素")
            value = list(value)
        elif not recordable(value):
            raise CalibrationError(code, f"观测字段 {key} 不是可记录的值（{type(value).__name__}）")
        leaves[str(key)] = value
    return leaves


def _arm_side_of_camera(entry: Any) -> str | None:
    """Which arm carries this camera, from its declared parent link.

    Read from the declaration rather than guessed, because the alternative is
    pairing a camera with the motion of an arm it is not attached to — which yields
    a plausible extrinsic that is wrong by a whole arm, and nothing downstream can
    tell.
    """
    parent = str(((entry or {}).get("extrinsics") or {}).get("parentLink") or "")
    for side in ARM_SIDES:
        if parent.startswith(side):
            return side
    return None


@dataclass
class WizardStep:
    id: str
    kind: str
    title: str
    instruction: str
    detail: str
    group: str
    motor: str | None = None
    motors: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    #: How to *show* this step, as opposed to what it asks for.
    #:
    #: The wizard owns the plan and the wording; the console owns the picture.
    #: They meet here rather than in either one, because a second definition of
    #: "what the operator is asked to do" drifts, and the one that drifts is the
    #: one on screen. ``kind`` names the animation; the rest are its parameters,
    #: and the front end renders a step it has no animation for by showing the
    #: words alone rather than nothing.
    guide: dict[str, Any] = field(default_factory=dict)

    def as_payload(self, status: str, index: int, total: int) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "index": index, "total": total,
            "title": self.title, "instruction": self.instruction, "detail": self.detail,
            "group": self.group, "motor": self.motor, "motors": list(self.motors),
            "requires": list(self.requires), "status": status,
            "guide": dict(self.guide or {}),
        }


def build_steps(cameras: Mapping[str, Any] | None = None) -> list[WizardStep]:
    """The complete guided plan.

    The order is the order of dependency, not of convenience. Nothing can be
    measured before the hardware is connected; the arms must be zeroed before
    hand-eye can pair an arm pose with what a camera saw; camera intrinsics come
    before the extrinsics that are solved against them.

    Camera steps are built from the cameras the robot itself declares — the mapping
    the document already carries, because it is the parent link in there that says
    whether a camera has a hand-eye question to answer at all. A unit with one
    camera gets one intrinsics step and a unit with three gets three. Demanding a
    fixed pair would make the flow wrong for every robot but the reference one —
    and the wizard is the last place that should assume a model.
    """
    cameras = dict(cameras or {})
    steps: list[WizardStep] = [
        WizardStep(
            id="connect", kind="connect", group="准备",
            title="把机器人接进这台电脑",
            instruction="按下面四项依次接好，每接好一项这台页面会自己确认。",
            detail=("标定要读舵机总线、也要读两个相机的画面，所以三路都得通。"
                    "系统会自己扫描端口，不需要你填。"),
            requires=tuple(check for check, _, _, _, _ in CONNECTION_STEPS),
            # ``id`` is the check the page reports back, ``port`` is what the cable
            # plugs into. They are not the same thing here: both cameras plug into
            # a "camera" port, so an id taken from the port kind would give the page
            # two indistinguishable entries and no way to say which one is in.
            guide={"kind": "connect", "ports": [
                {"id": check, "port": port, "label": title, "detail": detail, "expect": expect}
                for check, title, detail, port, expect in CONNECTION_STEPS]},
        ),
        WizardStep(
            id="preflight", kind="preflight", group="准备",
            title="开始前的安全检查",
            instruction="逐条确认下面四项，全部确认后才会开始标定。",
            detail="标定过程中机械臂会被用手移动。急停开关必须放在手边。",
            requires=tuple(check for check, _ in PREFLIGHT_CHECKS),
            guide={"kind": "preflight", "checks": [
                {"id": check, "label": label} for check, label in PREFLIGHT_CHECKS]},
        ),
    ]

    for group in ("left", "right", "shared"):
        motors = [name for name in MOTOR_IDS if MOTOR_BUS[name] == group]
        if group in ARM_SIDES:
            # Arm order follows the physical joint chain, which is how a person
            # moves along the arm.
            motors = [f"{group}_arm_{joint}" for joint in ARM_JOINTS]
        for motor in motors:
            joint = motor.split("_arm_", 1)[1] if "_arm_" in motor else motor
            label, pose = JOINT_GUIDE.get(joint, (joint, "把这一节转到它的中间位置"))
            steps.append(WizardStep(
                id=f"zero:{motor}", kind="zero", group=ARM_LABEL[group], motor=motor,
                title=f"{ARM_LABEL[group]} · {label}",
                instruction=f"用手{pose}，保持不动，然后确认。",
                detail=(f"总线 {MOTOR_BUS[motor]}，舵机 ID {MOTOR_IDS[motor]}。"
                        "只记录当前位置，不会驱动电机。"),
                guide={"kind": "zero", "motor": motor,
                       "side": group if group in ARM_SIDES else "shared", "joint": joint},
            ))
        if group in ARM_SIDES:
            steps.append(WizardStep(
                id=f"travel:{group}", kind="travel", group=ARM_LABEL[group],
                title=f"{ARM_LABEL[group]} · 活动范围",
                instruction=("用手扶着这条手臂，慢慢地来回移动到它能到达的两个极限，"
                             "来回两三次，然后确认。"),
                detail="系统会在这段时间里连续采样，自动记下每个关节的最小值和最大值。",
                motors=tuple(f"{group}_arm_{joint}" for joint in ARM_JOINTS),
                guide={"kind": "travel", "side": group,
                       "joints": list(ARM_JOINTS)},
            ))

    # --- what a calibration someone else can use actually needs ---------------
    #
    # A document with servos and no cameras is refused at the very end, after the
    # whole flow: the wizard used to say camera calibration "must be done
    # separately", which meant the guided flow could never produce a usable
    # document on a robot that had never been calibrated. These are the two
    # missing measurements, as steps with the same shape as the motor ones.
    for camera in sorted(cameras):
        steps.append(WizardStep(
            id=f"intrinsics:{camera}", kind="intrinsics", group="相机",
            title=f"{camera} · 内参",
            instruction=("把标定板举在这台相机前面，让整块板都在画面里，"
                         "然后按提示换几个角度和距离，每个角度停一下。"),
            detail=("内参是焦距和主点，与相机装在哪里无关，所以一台相机只需测一次。"
                    "板子要在画面里占够面积，倾斜角度不要超过大约 45 度。"),
            guide={"kind": "intrinsics", "camera": camera, "views": INTRINSICS_VIEWS},
        ))
    if cameras:
        # Only cameras bolted to an arm get a hand-eye step, and the step lists only
        # those. Hand-eye answers "where is this camera relative to the link that
        # carries it", so a camera on the head or the base has no such question to
        # answer: asking an operator to move an arm and photograph a board would
        # collect eight poses that no equation can use.
        arm_cameras = [camera for camera in sorted(cameras)
                       if _arm_side_of_camera(cameras[camera]) is not None]
        if arm_cameras:
            steps.append(WizardStep(
                id="handeye", kind="handeye", group="相机",
                title="手眼标定 · 相机装在手臂上的位置",
                instruction=("把标定板固定在桌面上不要动，然后手动把手臂带到几个不同姿态，"
                             "每个姿态让板子留在画面里再确认。"),
                detail=("这一步解的是相机相对于手臂末端的位置和朝向（外参）。"
                        "板子动了这一步就作废，所以要固定住；姿态要散开，"
                        "几个几乎一样的姿态解不出唯一答案。"),
                guide={"kind": "handeye", "cameras": list(arm_cameras), "poses": HANDEYE_POSES},
            ))

    steps.append(WizardStep(
        id="review", kind="review", group="完成",
        title="检查并保存",
        instruction="确认下面的参数，保存后立即生效。",
        detail="保存会生成一个新的标定版本号；之前采集的任务证据仍然指向旧版本，不会被改写。",
        guide={"kind": "review"},
    ))
    return steps


@dataclass
class WizardSession:
    """Persisted progress. Everything needed to resume lives here."""

    robot_id: str
    adapter_id: str
    started_at_unix_ms: int
    updated_at_unix_ms: int
    acknowledged: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    motors: dict[str, dict[str, int]] = field(default_factory=dict)
    cameras: dict[str, Any] = field(default_factory=dict)
    #: What the bus answered when the operator said the cables were in.
    connection: dict[str, Any] = field(default_factory=dict)
    #: Every board view accepted so far, keyed ``"<step kind>:<camera>"``.
    #:
    #: Views are kept, not summarised. A camera calibration is only as good as
    #: the frames behind it, and a session that stored a focal length without the
    #: observations could never be re-solved when the estimator improves or when
    #: someone has to explain why a number looks wrong.
    captures: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    geometry: dict[str, Any] | None = None
    safety: dict[str, Any] | None = None
    source: str = "measured"

    def as_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schemaVersion"] = SESSION_SCHEMA
        return payload


class CalibrationWizard:
    """Drives one guided calibration session against a hardware backend."""

    def __init__(self, hardware: CalibrationHardware, *, robot_id: str, adapter_id: str,
                 session_path: str | os.PathLike[str] | None = None,
                 base_document: dict[str, Any] | None = None,
                 solver: CalibrationSolver | None = None):
        self.hardware = hardware
        self.solver = solver
        self.robot_id = robot_id
        self.adapter_id = adapter_id
        self.session_path = Path(session_path) if session_path else None
        self.steps = build_steps((base_document or {}).get("cameras") or {})
        self.base_document = base_document
        if not (base_document or {}).get("cameras"):
            # Not a leftover limitation: the wizard measures intrinsics and solves
            # hand-eye itself, but it cannot deduce *that a camera exists* or which
            # link it is bolted to. Those two facts come from outside — a factory
            # sheet, an earlier session, or the simulator's model — and everything
            # else in the camera half is measured here.
            raise CalibrationError(
                "CAMERAS_REQUIRED",
                "引导式标定需要先声明这台机器人装了哪些相机、各装在哪个连杆上"
                "（出厂清单、上一次标定结果或仿真模型推导值）；"
                "内参与手眼外参由本次流程测量，但相机的数量与安装位置不能由标定反推",
            )
        self.session = self._load_session() or WizardSession(
            robot_id=robot_id, adapter_id=adapter_id,
            started_at_unix_ms=int(time.time() * 1000),
            updated_at_unix_ms=int(time.time() * 1000),
            # Deep-copied, not shallow: a shallow copy shares the per-camera dicts
            # with the caller, so writing a solved intrinsic would reach back into
            # the live document the console is holding — before anything was saved
            # and before anything was validated. A wizard that edits its input is a
            # wizard whose refusals cannot be trusted.
            cameras=copy.deepcopy((base_document or {}).get("cameras") or {}),
            geometry=copy.deepcopy((base_document or {}).get("geometry")),
            safety=copy.deepcopy((base_document or {}).get("safety")),
            source=("simulation" if hardware.describe().get("backend") == "simulated"
                    else "measured" if (base_document or {}).get("source") == "measured" else "default"),
        )
        if hardware.describe().get("backend") != "simulated" and self.session.source == "simulation":
            raise CalibrationError("SOURCE_MISMATCH", "仿真标定会话不能用于真机；请建立新的标定会话")
        # Written now, not at the first completed step. The page reads this file to
        # learn the plan, and an operator who has just opened the wizard should see
        # all 23 steps and which one is current — waiting until they have already done
        # something to say what to do next is backwards.
        self._persist()

    # -- progress -----------------------------------------------------------

    def _load_session(self) -> WizardSession | None:
        if self.session_path is None or not self.session_path.is_file():
            return None
        raw = json.loads(self.session_path.read_text(encoding="utf-8"))
        if raw.get("schemaVersion") != SESSION_SCHEMA:
            raise CalibrationError("SESSION_SCHEMA", "saved calibration session is from another version")
        if raw.get("robot_id") != self.robot_id:
            raise CalibrationError("ROBOT_MISMATCH",
                                   f"saved session belongs to {raw.get('robot_id')}, not {self.robot_id}")
        return WizardSession(**{key: value for key, value in raw.items() if key != "schemaVersion"})

    @property
    def status_path(self) -> Path | None:
        """Where the console reads progress, so the UI never re-implements the plan.

        The step order, the wording and the summary live here, in one language. The
        front end renders this document; it does not own a second copy of the flow
        that could drift out of step with what the robot actually does.
        """
        if self.session_path is None:
            return None
        return self.session_path.with_suffix(".status.json")

    def _persist(self) -> None:
        if self.session_path is None:
            return
        self.session.updated_at_unix_ms = int(time.time() * 1000)
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_atomic(self.session_path, json.dumps(
            self.session.as_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        status_path = self.status_path
        if status_path is not None:
            self._write_atomic(status_path, json.dumps(
                self.status(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _write_atomic(path: Path, payload: str) -> None:
        """Write beside the target and rename, so a reader never sees half a file."""
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def status(self) -> dict[str, Any]:
        """Everything a UI needs: the plan, what is done, and the next action."""
        done = set(self.session.completed)
        payload_steps = []
        current = None
        for index, step in enumerate(self.steps):
            state = "done" if step.id in done else "pending"
            if current is None and state == "pending":
                state, current = "current", step
            payload_steps.append(step.as_payload(state, index + 1, len(self.steps)))
        return {
            "robotId": self.robot_id,
            "adapterId": self.adapter_id,
            "steps": payload_steps,
            "completed": len(done),
            "total": len(self.steps),
            "next": current.as_payload("current", 0, len(self.steps)) if current else None,
            "finished": current is None,
            "blockingProblems": list(self.hardware.blocking_problems()),
            "hardware": self.hardware.describe(),
            "summary": self.summary_text(),
            "provenance": self.provenance(),
        }

    def provenance(self) -> dict[str, Any]:
        """Which numbers this session took on this unit, and which it inherited.

        A calibration is worth what its weakest inherited number is worth, so the page
        has to be able to say "this camera's mounting still comes from the factory
        sheet" out loud. Without this a document looks uniformly measured, and the one
        number nobody verified is the one nobody looks at.
        """
        measured_intrinsics: list[str] = []
        measured_extrinsics: list[str] = []
        for name in sorted(self.session.cameras):
            if len(self.session.captures.get(f"intrinsics:{name}", [])) >= INTRINSICS_VIEWS:
                measured_intrinsics.append(name)
            if len(self.session.captures.get(f"handeye:{name}", [])) >= HANDEYE_POSES:
                measured_extrinsics.append(name)
        cameras = sorted(self.session.cameras)
        return {
            "motorsMeasured": sorted(self.session.motors),
            "intrinsicsMeasured": measured_intrinsics,
            "extrinsicsMeasured": measured_extrinsics,
            "intrinsicsCarriedOver": [n for n in cameras if n not in measured_intrinsics],
            "extrinsicsCarriedOver": [n for n in cameras if n not in measured_extrinsics],
        }

    def next_step(self) -> WizardStep | None:
        done = set(self.session.completed)
        for step in self.steps:
            if step.id not in done:
                return step
        return None

    # -- execution ----------------------------------------------------------

    def acknowledge(self, step_id: str, checks: dict[str, bool]) -> None:
        """Record a human confirmation, and verify what the robot can verify itself.

        The two acknowledge-style steps differ in exactly one way, and it is the
        interesting part. ``preflight`` is about the room — a program cannot know
        whether the emergency stop is within reach, so it takes the operator's
        word. ``connect`` is about the unit — the operator says which cables are
        in, and then the bus is actually read, because "已插好" is a belief and an
        answering encoder is a fact.
        """
        step = self._step(step_id)
        if step.kind == "preflight":
            self._require_checks(step, checks, PREFLIGHT_CHECKS, "PREFLIGHT_INCOMPLETE",
                                 "以下检查未确认，标定不会开始：")
            problems = self.hardware.blocking_problems()
            if problems:
                raise CalibrationError("HARDWARE_NOT_READY", "；".join(problems))
            self.session.acknowledged = list(step.requires)
        elif step.kind == "connect":
            self._require_checks(step, checks, CONNECTION_STEPS, "CONNECTION_INCOMPLETE",
                                 "以下连接未确认：")
            self.session.connection = self._probe_connection()
        else:
            raise CalibrationError("WRONG_STEP", f"{step_id} 不是需要人工逐条确认的步骤")
        self._complete(step.id)

    @staticmethod
    def _require_checks(step: WizardStep, checks: dict[str, bool],
                        catalogue: Sequence[tuple], code: str, prefix: str) -> None:
        missing = [check for check in step.requires if not checks.get(check)]
        if not missing:
            return
        labels = {check: label for check, label, *_ in catalogue}
        raise CalibrationError(code, prefix + "、".join(labels.get(check, check) for check in missing))

    def _probe_connection(self) -> dict[str, Any]:
        """Read one encoder on every bus, so "connected" is a measurement.

        A description is not evidence: a backend happily lists port names for
        hardware that was unplugged an hour ago. A servo that answers with a
        plausible count is the smallest fact that cannot be produced without the
        cable actually being in.
        """
        description = self.hardware.describe()
        buses: dict[str, Any] = {}
        for group in ("left", "right", "shared"):
            motor = next((name for name in MOTOR_IDS if MOTOR_BUS[name] == group), None)
            if motor is None:
                continue
            try:
                position = self.hardware.read_motor_position(motor)
            except Exception as error:  # a bus that raises is a bus that is not there
                raise CalibrationError(
                    "HARDWARE_NOT_READY",
                    f"{ARM_LABEL[group]}的舵机总线没有回应：{error}") from error
            if isinstance(position, bool) or not isinstance(position, int) or not 0 <= position <= 4095:
                raise CalibrationError(
                    "HARDWARE_NOT_READY",
                    f"{ARM_LABEL[group]}的舵机总线没有回应（读到 {position!r}）；"
                    "请检查数据线与电源，然后重新确认这一步",
                )
            buses[group] = {"motor": motor, "position": position}
        declared = description.get("cameras")
        return {
            "backend": description.get("backend"),
            "buses": buses,
            # Not "ok": the backend did not claim to know its cameras, and a
            # wizard that wrote "ok" here would be inventing a check. The camera
            # is genuinely verified one step later, when a frame is captured.
            "cameras": (sorted(declared) if isinstance(declared, (list, tuple, dict))
                        else "unverified"),
        }

    def confirm(self, step_id: str, *, travel_seconds: float = 0.0,
                camera: str | None = None) -> dict[str, Any]:
        """Perform one step and record exactly what the hardware reported.

        One call is one *unit* of work, and the unit differs by step because the
        work does. A zero is one reading taken now; a sweep is a burst of readings
        over time; a board view is one frame from one pose. Camera steps therefore
        return progress and are called once per view, which is also how the page
        wants to drive them: press, see 3/12, move, press again.
        """
        step = self._step(step_id)
        if step.id in self.session.completed:
            return {"step": step.id, "alreadyDone": True}
        if step.kind == "preflight" or step.kind == "connect":
            raise CalibrationError("WRONG_STEP", f"{step.kind} needs acknowledge(), not confirm()")
        if step.kind == "zero":
            assert step.motor is not None
            position = self.hardware.read_motor_position(step.motor)
            self._require_encoder(position, step.motor)
            existing = self.session.motors.get(step.motor)
            self.session.motors[step.motor] = {
                # The zero capture is the homing offset; range comes from the sweep.
                "id": MOTOR_IDS[step.motor],
                "drive_mode": (existing or {}).get("drive_mode", 0),
                "homing_offset": 0 if step.motor.startswith("base_") else position - 2048,
                "range_min": (existing or {}).get("range_min", 0),
                "range_max": (existing or {}).get("range_max", 4095),
            }
        elif step.kind == "travel":
            self._sample_travel(step, travel_seconds=travel_seconds)
        elif step.kind in ("intrinsics", "handeye"):
            return self._capture_view(step, camera=camera)
        self._complete(step.id)
        return {"step": step.id, "alreadyDone": False, "motors": dict(self.session.motors)}

    # -- camera steps -------------------------------------------------------

    def _capture_view(self, step: WizardStep, *, camera: str | None) -> dict[str, Any]:
        """Take one board view towards an intrinsics or hand-eye step.

        The step is finished by *reaching the required count and solving*, never
        by a single frame: no method recovers a focal length from one view, and a
        wizard that marked the step done at the first press would be reporting
        progress it had not made. Everything captured is kept in the session, so a
        refusal here costs movement, not measurements.
        """
        needed = INTRINSICS_VIEWS if step.kind == "intrinsics" else HANDEYE_POSES
        target = camera or self._camera_still_needing_views(step, needed)
        if target is None:
            # Every declared camera already holds enough views, which is precisely the
            # state a session is left in when it was captured without a solver. Solve
            # from what is on disk instead of asking for a thirteenth pose of a
            # twelve-pose step: the capturing is what cost the operator a trip, and it
            # is already done.
            target = self._camera_awaiting_solve(step, needed)
            if target is None:
                raise CalibrationError("UNKNOWN_STEP", f"{step.id} 没有可用的相机")
        if target not in self.session.cameras:
            raise CalibrationError(
                "UNKNOWN_CAMERA", f"{target} 不在本次标定的相机清单里")
        key = f"{step.kind}:{target}"
        # Looked up rather than created: recording an empty list for a frame where no
        # board was found would put a view that never happened into the session file
        # the page renders from.
        views = self.session.captures.get(key, [])
        if len(views) >= needed:
            solved = self._solve(step, camera=target, views=views)
            self._complete(step.id)
            return {"step": step.id, "alreadyDone": False, "camera": target,
                    "views": len(views), "needed": needed, "done": True, "solved": solved}
        pose = self._arm_pose_for(target) if step.kind == "handeye" else None
        if step.kind == "handeye" and pose is None:
            return self._handeye_needs_mount(step, target)

        capture = getattr(self.hardware, "capture_calibration_view", None)
        if capture is None:
            raise CalibrationError(
                "CAPTURE_UNAVAILABLE",
                f"当前硬件后端（{self.hardware.describe().get('backend', '未命名')}）"
                f"不能读取相机画面，无法完成「{step.title}」。"
                "请换用实现了 capture_calibration_view 的后端；"
                "否则相机的内参与手眼外参只能由外部工具单独标定。",
            )
        view = capture(target)
        if not view:
            raise CalibrationError(
                "BOARD_NOT_FOUND",
                "这一帧没有认出标定板。让整块板都在画面里、避免反光与运动模糊，再试一次。",
            )
        record: dict[str, Any] = {
            "camera": target,
            "atUnixMs": int(time.time() * 1000),
            "observed": _leaf_fields(view, code="CAPTURE_INVALID"),
        }
        if pose is not None:
            # The pose is what makes the pair a pair: a hand-eye view without the
            # arm position it was taken at is unanswerable, and storing the view
            # first would leave a session that looks complete and cannot be solved.
            record["pose"] = pose
        views.append(record)
        self.session.captures[key] = views
        have = len(views)
        # Written out before any solve is attempted. Capturing is the part that costs
        # a person a walk to the robot; solving is arithmetic that can be redone. A
        # solver that refuses, or a machine that dies mid-solve, must not take the
        # last pose down with it.
        self._persist()
        if have < needed:
            return {"step": step.id, "alreadyDone": False, "camera": target,
                    "views": have, "needed": needed, "done": False}

        solved = self._solve(step, camera=target, views=views)
        self._complete(step.id)
        return {"step": step.id, "alreadyDone": False, "camera": target,
                "views": have, "needed": needed, "done": True, "solved": solved}

    def _declared_cameras(self, step: WizardStep) -> list[str]:
        """The cameras this step measures, from the plan rather than from the session."""
        if step.kind == "intrinsics":
            name = step.guide.get("camera")
            return [str(name)] if name else []
        return [str(name) for name in (step.guide.get("cameras") or []) if name]

    def _camera_still_needing_views(self, step: WizardStep, needed: int) -> str | None:
        return next((name for name in self._declared_cameras(step)
                     if len(self.session.captures.get(f"{step.kind}:{name}", [])) < needed), None)

    def _camera_awaiting_solve(self, step: WizardStep, needed: int) -> str | None:
        return next((name for name in self._declared_cameras(step)
                     if len(self.session.captures.get(f"{step.kind}:{name}", [])) >= needed), None)

    def _handeye_needs_mount(self, step: WizardStep, camera: str) -> dict[str, Any]:
        """Refuse to pair a camera with motion of an arm it is not mounted on.

        Hand-eye solves for the camera relative to the link under it, so the poses
        that pair with its views must be *that* arm's. Guessing a side would
        produce a plausible-looking extrinsic that is wrong by a whole arm — the
        most expensive kind of wrong, because nothing downstream can detect it.
        """
        raise CalibrationError(
            "CAMERA_MOUNT_UNKNOWN",
            f"无法确定 {camera} 装在哪条手臂上，因此无法配对手眼标定所需的手臂位姿。"
            "请在相机清单里给出 extrinsics.parentLink（例如 left_arm_link6），再重来这一步。",
        )

    def _arm_pose_for(self, camera: str) -> dict[str, int] | None:
        """Every joint of the arm carrying ``camera``, read now."""
        side = _arm_side_of_camera(self.session.cameras.get(camera))
        if side is None:
            return None
        pose: dict[str, int] = {}
        for joint in ARM_JOINTS:
            motor = f"{side}_arm_{joint}"
            position = self.hardware.read_motor_position(motor)
            self._require_encoder(position, motor)
            pose[motor] = position
        return pose

    def _solve(self, step: WizardStep, *, camera: str, views: list[dict[str, Any]]) -> dict[str, Any]:
        """Hand the kept views to the solver, or say precisely which piece is missing."""
        if step.kind == "intrinsics":
            if self.solver is None:
                raise CalibrationError(
                    "SOLVER_UNAVAILABLE",
                    f"{camera} 的 {len(views)} 个视角已经全部保留在会话里，"
                    "但这套网关没有配置内参解算器（CalibrationSolver.solve_intrinsics）。"
                    "视角不会丢失：接上解算器后可以直接从同一份会话继续，不需要重新拍摄。",
                )
            intrinsics = self.solver.solve_intrinsics(camera, views)
            entry = self.session.cameras.setdefault(camera, {})
            entry["intrinsics"] = dict(intrinsics)
            return {"intrinsics": dict(intrinsics)}
        if self.solver is None:
            raise CalibrationError(
                "SOLVER_UNAVAILABLE",
                f"{camera} 的 {len(views)} 个手眼视角已经全部保留在会话里，"
                "但这套网关没有配置手眼解算器（CalibrationSolver.solve_handeye）。"
                "视角不会丢失：接上解算器后可以直接从同一份会话继续，不需要重新拍摄。",
            )
        # The three things the solve needs that only the session knows: the lens this
        # camera was just measured with, the motor document in force (a count is not an
        # angle without its zero), and the link the extrinsic is expressed in.
        declared = self.session.cameras.get(camera) or {}
        extrinsics = self.solver.solve_handeye(
            views,
            intrinsics=declared.get("intrinsics"),
            motors=self.session.motors,
            parent_link=(declared.get("extrinsics") or {}).get("parentLink"),
        )
        entry = self.session.cameras.setdefault(camera, {})
        entry["extrinsics"] = dict(extrinsics)
        return {"extrinsics": dict(extrinsics)}

    def _sample_travel(self, step: WizardStep, *, travel_seconds: float) -> None:
        """Record min/max across a sweep, never below what a real joint can reach."""
        samples: dict[str, list[int]] = {motor: [] for motor in step.motors}
        deadline = time.monotonic() + max(travel_seconds, 0.0)
        # Two readings is the minimum that can show movement: a single sample would
        # always report min == max and fail every sweep that actually worked.
        while True:
            for motor in step.motors:
                position = self.hardware.read_motor_position(motor)
                self._require_encoder(position, motor)
                samples[motor].append(position)
            if travel_seconds <= 0:
                if all(len(readings) >= 2 for readings in samples.values()):
                    break
            elif time.monotonic() >= deadline:
                break
            else:
                time.sleep(0.05)
        for motor, readings in samples.items():
            entry = self.session.motors.setdefault(motor, {
                "id": MOTOR_IDS[motor], "drive_mode": 0, "homing_offset": 0,
                "range_min": 0, "range_max": 4095,
            })
            low, high = min(readings), max(readings)
            if low >= high:
                raise CalibrationError(
                    "NO_TRAVEL",
                    f"{ARM_LABEL[MOTOR_BUS[motor]]} 的 {motor} 没有检测到移动；"
                    "请扶着这条手臂慢慢移动到两个极限后重试",
                )
            entry["range_min"], entry["range_max"] = low, high

    @staticmethod
    def _require_encoder(position: Any, motor: str) -> None:
        if isinstance(position, bool) or not isinstance(position, int) or not 0 <= position <= 4095:
            raise CalibrationError("INVALID_READING", f"{motor} 返回了无法识别的读数 {position!r}")

    def _step(self, step_id: str) -> WizardStep:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise CalibrationError("UNKNOWN_STEP", f"没有名为 {step_id} 的步骤")

    def _complete(self, step_id: str) -> None:
        if step_id not in self.session.completed:
            self.session.completed.append(step_id)
        self._persist()

    # -- output -------------------------------------------------------------

    def document(self) -> dict[str, Any]:
        """Assemble a calibratable document; incomplete sessions are refused."""
        pending = [step.id for step in self.steps if step.id not in set(self.session.completed)]
        if pending:
            raise CalibrationError("SESSION_INCOMPLETE",
                                   f"还有 {len(pending)} 步未完成，例如 {pending[0]}")
        return validate_calibration({
            "schemaVersion": "robot.calibration.v1",
            "robotId": self.robot_id,
            "adapterId": self.adapter_id,
            "source": self.session.source,
            "updatedAtUnixMs": int(time.time() * 1000),
            "motors": self.session.motors,
            "cameras": self.session.cameras,
            "geometry": self.session.geometry,
            "safety": self.session.safety,
        })

    def summary_text(self) -> str:
        """One plain-language sentence about where the session stands."""
        done = set(self.session.completed)
        remaining = [step for step in self.steps if step.id not in done]
        arms = {}
        for step in self.steps:
            if step.kind != "zero" or step.id in done:
                continue
            arms[step.group] = arms.get(step.group, 0) + 1
        if not remaining:
            return (f"标定已完成：{len(self.session.motors)} 个舵机、"
                    f"{len(self.session.cameras)} 个相机。保存后即可使用。")
        if not done:
            return f"尚未开始。共 {len(self.steps)} 步，预计需要十几分钟。"
        parts = [f"已完成 {len(done)}/{len(self.steps)} 步"]
        if arms:
            parts.append("还差 " + "、".join(f"{group} {count} 个关节" for group, count in arms.items()))
        parts.append(f"下一步：{remaining[0].title}")
        return "；".join(parts) + "。"

    def close(self) -> None:
        self.hardware.close()


class SimulatedCalibrationHardware:
    """A backend that behaves like a freshly assembled unit, for tests and demos.

    Positions are deterministic per motor, so a session can be replayed; the
    ``blocking_problems`` list can be seeded to exercise the refusal paths.
    """

    def __init__(self, *, problems: list[str] | None = None, travel: int = 900,
                 fail_reads: tuple[str, ...] = (), cameras: Mapping[str, Any] | None = None):
        self.problems = list(problems or [])
        # The declared cameras, so the simulated lens fits the frame each one really
        # has. Without them a 640x480 lens would be aimed at a 320x240 sensor and the
        # solve would be graded against a camera the unit does not have.
        self.cameras = dict(cameras or {})
        self.travel = travel
        self.fail_reads = set(fail_reads)
        self.closed = False
        self._sweep: dict[str, int] = {}
        self.reads: list[str] = []

    def describe(self) -> dict[str, Any]:
        return {"backend": "simulated", "motors": len(MOTOR_IDS),
                "buses": {"left": "/dev/tangying-left", "right": "/dev/tangying-right"}}

    def blocking_problems(self) -> list[str]:
        return list(self.problems)

    def read_motor_position(self, motor: str) -> int:
        self.reads.append(motor)
        if motor in self.fail_reads:
            return -1
        index = list(MOTOR_IDS).index(motor)
        # Sweeping back and forth across a plausible travel keeps min/max distinct.
        step = self._sweep.get(motor, 0)
        self._sweep[motor] = (step + 1) % 4
        base = 500 + index * 90
        return base + (0, self.travel, self.travel // 2, 0)[step]

    def close(self) -> None:
        self.closed = True

    # -- the camera half ----------------------------------------------------
    #
    # A simulator's job is to produce data that is synthetic but *self-consistent*:
    # the board poses below are generated from this class's own camera, nothing is
    # read back from the document being produced, and a solver that recovers
    # ``SIMULATED_CAMERA`` has therefore recovered something real. The session the
    # flow writes is stamped ``source: "simulation"`` and can never be mistaken for
    # a measurement taken on a physical unit.

    def capture_calibration_view(self, camera: str) -> dict[str, Any] | None:
        """Project the simulated board through the simulated lens, once per call."""
        from .calibration_solver import (
            SIMULATED_BOARD_COLUMNS,
            SIMULATED_BOARD_ROWS,
            SIMULATED_BOARD_SQUARE_M,
            simulated_camera,
        )

        counts = getattr(self, "_captures", None)
        if counts is None:
            counts = self._captures = {}
        index = counts.get(camera, 0)
        counts[camera] = index + 1
        declared = self.cameras.get(camera) or {}
        lens = simulated_camera(int(declared.get("width") or 640),
                                int(declared.get("height") or 480))
        rx, ry, rz, tx, ty, tz = _SIMULATED_BOARD_POSES[index % len(_SIMULATED_BOARD_POSES)]
        board = [
            (column * SIMULATED_BOARD_SQUARE_M, row * SIMULATED_BOARD_SQUARE_M)
            for row in range(SIMULATED_BOARD_ROWS) for column in range(SIMULATED_BOARD_COLUMNS)
        ]
        rotation = _matmul3(_matmul3(_rotation_z(rz), _rotation_y(ry)), _rotation_x(rx))
        offset = (tx, ty, tz)
        object_points: list[float] = []
        image_points: list[float] = []
        for x, y in board:
            point = [
                rotation[0][0] * x + rotation[0][1] * y + offset[0],
                rotation[1][0] * x + rotation[1][1] * y + offset[1],
                rotation[2][0] * x + rotation[2][1] * y + offset[2],
            ]
            if point[2] <= 1e-6:
                return None
            u = lens["fx"] * point[0] / point[2] + lens["cx"]
            v = lens["fy"] * point[1] / point[2] + lens["cy"]
            object_points.extend((x, y))
            image_points.extend((u, v))
        return {
            "objectPoints": object_points,
            "imagePoints": image_points,
            "boardColumns": SIMULATED_BOARD_COLUMNS,
            "boardRows": SIMULATED_BOARD_ROWS,
            "squareM": SIMULATED_BOARD_SQUARE_M,
        }


#: Board poses the simulated camera sees, one per call. They sweep tilt and distance
#: because that is what a real operator is asked to do, and because a set of nearly
#: identical views is exactly what a solve must refuse.
_SIMULATED_BOARD_POSES = (
    (0.15, 0.20, -0.10, 0.020, -0.010, 0.55),
    (-0.25, 0.10, 0.30, -0.040, 0.030, 0.62),
    (0.35, -0.30, 0.20, 0.050, 0.020, 0.48),
    (-0.10, -0.20, -0.40, 0.000, -0.050, 0.70),
    (0.20, 0.40, 0.15, -0.030, 0.040, 0.58),
    (-0.30, 0.25, -0.25, 0.010, 0.000, 0.66),
    (0.05, -0.35, 0.35, -0.020, -0.030, 0.52),
    (0.30, 0.05, 0.45, 0.040, 0.050, 0.60),
    (-0.20, 0.35, 0.10, -0.050, -0.020, 0.45),
    (0.40, -0.15, -0.20, 0.030, 0.010, 0.72),
    (0.10, 0.30, 0.25, 0.000, 0.020, 0.68),
    (-0.35, -0.05, 0.05, -0.010, 0.030, 0.56),
    (0.25, -0.25, -0.30, 0.025, -0.045, 0.64),
    (-0.15, -0.40, 0.20, -0.025, 0.015, 0.50),
)


def _matmul3(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    """3×3 matrix product, written out so the wizard needs no array library."""
    return [[sum(left[row][k] * right[k][column] for k in range(3)) for column in range(3)]
            for row in range(3)]


def _rotation_x(angle: float) -> list[list[float]]:
    return [[1.0, 0.0, 0.0],
            [0.0, math.cos(angle), -math.sin(angle)],
            [0.0, math.sin(angle), math.cos(angle)]]


def _rotation_y(angle: float) -> list[list[float]]:
    return [[math.cos(angle), 0.0, math.sin(angle)],
            [0.0, 1.0, 0.0],
            [-math.sin(angle), 0.0, math.cos(angle)]]


def _rotation_z(angle: float) -> list[list[float]]:
    return [[math.cos(angle), -math.sin(angle), 0.0],
            [math.sin(angle), math.cos(angle), 0.0],
            [0.0, 0.0, 1.0]]
