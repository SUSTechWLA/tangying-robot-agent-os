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

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

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


def build_steps(cameras: Sequence[str] = ()) -> list[WizardStep]:
    """The complete guided plan.

    The order is the order of dependency, not of convenience. Nothing can be
    measured before the hardware is connected; the arms must be zeroed before
    hand-eye can pair an arm pose with what a camera saw; camera intrinsics come
    before the extrinsics that are solved against them.

    Camera steps are built from the cameras the robot itself declares, so a unit
    with one camera gets one intrinsics step and a unit with three gets three.
    Demanding a fixed pair would make the flow wrong for every robot but the
    reference one — and the wizard is the last place that should assume a model.
    """
    steps: list[WizardStep] = [
        WizardStep(
            id="connect", kind="connect", group="准备",
            title="把机器人接进这台电脑",
            instruction="按下面四项依次接好，每接好一项这台页面会自己确认。",
            detail=("标定要读舵机总线、也要读两个相机的画面，所以三路都得通。"
                    "系统会自己扫描端口，不需要你填。"),
            requires=tuple(check for check, _, _, _, _ in CONNECTION_STEPS),
            guide={"kind": "connect", "ports": [
                {"id": port, "label": title, "detail": detail, "expect": expect}
                for _, title, detail, port, expect in CONNECTION_STEPS]},
        ),
        WizardStep(
            id="preflight", kind="preflight", group="准备",
            title="开始前的安全检查",
            instruction="逐条确认下面四项，全部确认后才会开始标定。",
            detail="标定过程中机械臂会被用手移动。急停开关必须放在手边。",
            requires=tuple(check for check, _ in PREFLIGHT_CHECKS),
            guide={"kind": "preflight", "checks": [check for check, _ in PREFLIGHT_CHECKS]},
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
    for camera in cameras:
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
        steps.append(WizardStep(
            id="handeye", kind="handeye", group="相机",
            title="手眼标定 · 相机装在手臂上的位置",
            instruction=("把标定板固定在桌面上不要动，然后手动把手臂带到几个不同姿态，"
                         "每个姿态让板子留在画面里再确认。"),
            detail=("这一步解的是相机相对于手臂末端的位置和朝向（外参）。"
                    "板子动了这一步就作废，所以要固定住；姿态要散开，"
                    "几个几乎一样的姿态解不出唯一答案。"),
            guide={"kind": "handeye", "cameras": list(cameras), "poses": HANDEYE_POSES},
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
                 base_document: dict[str, Any] | None = None):
        self.hardware = hardware
        self.robot_id = robot_id
        self.adapter_id = adapter_id
        self.session_path = Path(session_path) if session_path else None
        self.steps = build_steps(sorted((base_document or {}).get("cameras") or {}))
        self.base_document = base_document
        if not (base_document or {}).get("cameras"):
            # The guided flow measures servos. Camera intrinsics and mounting come
            # from a factory sheet, an earlier session or the simulator's model;
            # without them a saved document would be rejected at the very end,
            # after twenty minutes of work.
            raise CalibrationError(
                "CAMERAS_REQUIRED",
                "引导式标定需要一份已有的相机参数作为基础（出厂参数、上一次标定结果或仿真模型推导值）；"
                "相机标定目前需要单独完成",
            )
        self.session = self._load_session() or WizardSession(
            robot_id=robot_id, adapter_id=adapter_id,
            started_at_unix_ms=int(time.time() * 1000),
            updated_at_unix_ms=int(time.time() * 1000),
            cameras=dict((base_document or {}).get("cameras") or {}),
            geometry=(base_document or {}).get("geometry"),
            safety=(base_document or {}).get("safety"),
            source=("simulation" if hardware.describe().get("backend") == "simulated"
                    else "measured" if (base_document or {}).get("source") == "measured" else "default"),
        )
        if hardware.describe().get("backend") != "simulated" and self.session.source == "simulation":
            raise CalibrationError("SOURCE_MISMATCH", "仿真标定会话不能用于真机；请建立新的标定会话")

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
        }

    def next_step(self) -> WizardStep | None:
        done = set(self.session.completed)
        for step in self.steps:
            if step.id not in done:
                return step
        return None

    # -- execution ----------------------------------------------------------

    def acknowledge(self, step_id: str, checks: dict[str, bool]) -> None:
        """Record the preflight acknowledgements; refuses while hardware is unusable."""
        step = self._step(step_id)
        if step.kind != "preflight":
            raise CalibrationError("WRONG_STEP", f"{step_id} is not a preflight step")
        missing = [check for check in step.requires if not checks.get(check)]
        if missing:
            raise CalibrationError(
                "PREFLIGHT_INCOMPLETE",
                "以下检查未确认，标定不会开始：" + "、".join(
                    dict(PREFLIGHT_CHECKS)[check] for check in missing),
            )
        problems = self.hardware.blocking_problems()
        if problems:
            raise CalibrationError("HARDWARE_NOT_READY", "；".join(problems))
        self.session.acknowledged = list(step.requires)
        self._complete(step.id)

    def confirm(self, step_id: str, *, travel_seconds: float = 0.0) -> dict[str, Any]:
        """Perform one step and record exactly what the hardware reported."""
        step = self._step(step_id)
        if step.id in self.session.completed:
            return {"step": step.id, "alreadyDone": True}
        if step.kind == "preflight":
            raise CalibrationError("WRONG_STEP", "preflight needs acknowledge(), not confirm()")
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
        self._complete(step.id)
        return {"step": step.id, "alreadyDone": False, "motors": dict(self.session.motors)}

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
                 fail_reads: tuple[str, ...] = ()):
        self.problems = list(problems or [])
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
