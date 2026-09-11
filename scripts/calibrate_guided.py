#!/usr/bin/env python3
"""Run the guided robot calibration. Written for the person holding the robot.

    scripts/calibrate_guided.py --robot-id xlerobot-01            # follow the prompts
    scripts/calibrate_guided.py --list                            # show the plan only
    scripts/calibrate_guided.py --robot-id xlerobot-01 --resume    # continue a session
    scripts/calibrate_guided.py --simulate                        # rehearse without a robot

The guided flow measures the servos. Camera parameters come from the base
calibration (factory sheet, a previous session, or the simulator's model), passed
with ``--base`` or defaulting to the file the runtime already uses.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot/gateway"))

from tangying_robot_gateway.calibration import (
    CalibrationError,
    calibration_revision,
    load_calibration,
    save_calibration,
)
from tangying_robot_gateway.calibration_wizard import (
    PREFLIGHT_CHECKS,
    CalibrationWizard,
    SimulatedCalibrationHardware,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--robot-id", default="xlerobot-01")
    parser.add_argument("--adapter-id", default="xlerobot")
    parser.add_argument("--base", type=Path, default=None,
                        help="existing calibration that supplies camera parameters")
    parser.add_argument("--output", type=Path, default=None,
                        help="where to write the finished calibration")
    parser.add_argument("--session", type=Path, default=None, help="session file (default: alongside --output)")
    parser.add_argument("--list", action="store_true", help="print the plan and exit")
    parser.add_argument("--resume", action="store_true", help="continue a saved session without re-asking preflight")
    parser.add_argument("--simulate", action="store_true",
                        help="rehearse the whole flow against a simulated robot (no hardware)")
    parser.add_argument("--travel-seconds", type=float, default=15.0,
                        help="how long to sample each arm sweep")
    return parser.parse_args()


def show_plan(wizard: CalibrationWizard) -> None:
    status = wizard.status()
    print(f"\n{status['summary']}\n")
    for step in status["steps"]:
        mark = "✓" if step["status"] == "done" else "·"
        print(f" {mark} {step['index']:>2}. [{step['group']}] {step['title']}")
        print(f"      {step['instruction']}")
        if step["detail"]:
            print(f"      （{step['detail']}）")


def main() -> int:
    args = parse_args()

    base = load_calibration(args.base) if args.base else None
    if base is None:
        print("需要一份包含相机参数的已有标定：用 --base 指定文件（出厂参数、上次标定或仿真推导值）。",
              file=sys.stderr)
        return 2

    # Printing the plan reads nothing, so it must not require a robot to be attached.
    hardware = (SimulatedCalibrationHardware() if (args.simulate or args.list)
                else _open_hardware(args))
    output = args.output or Path(f"{args.robot_id}.calibration.json")
    session = args.session or output.with_suffix(".session.json")

    try:
        wizard = CalibrationWizard(hardware, robot_id=args.robot_id, adapter_id=args.adapter_id,
                                   session_path=session, base_document=base)
    except CalibrationError as error:
        print(f"无法开始标定：{error.message}", file=sys.stderr)
        return 2

    try:
        if args.list:
            show_plan(wizard)
            return 0

        if not args.resume and wizard.next_step() and wizard.next_step().kind == "preflight":
            print(f"\n标定开始前，请逐条确认（共 {len(PREFLIGHT_CHECKS)} 项）：")
            checks = {}
            for check, question in PREFLIGHT_CHECKS:
                answer = input(f"  · {question}？ [y/N] ").strip().lower()
                checks[check] = answer in {"y", "yes"}
            try:
                wizard.acknowledge("preflight", checks)
            except CalibrationError as error:
                print(f"\n暂不能开始：{error.message}", file=sys.stderr)
                return 2

        while (step := wizard.next_step()) is not None:
            print(f"\n[{step.group}] {step.title}")
            print(f"  {step.instruction}")
            if step.detail:
                print(f"  （{step.detail}）")
            if step.kind == "review":
                print(json.dumps(wizard.session.motors, ensure_ascii=False, indent=2)[:2000])
            answer = input("  完成后按回车；输入 s 保存进度稍后继续，输入 q 放弃：").strip().lower()
            if answer == "q":
                print(f"\n已保存进度到 {session}，可以随时用 --resume 继续。")
                return 1
            if answer == "s":
                print(f"\n已保存进度到 {session}。{wizard.summary_text()}")
                return 0
            try:
                wizard.confirm(step.id, travel_seconds=args.travel_seconds if step.kind == "travel" else 0.0)
            except CalibrationError as error:
                print(f"\n这一步没有完成：{error.message}", file=sys.stderr)
                continue

        document = wizard.document()
        save_calibration(output, document)
        print(f"\n{wizard.summary_text()}")
        print(f"已保存到 {output}")
        print(f"标定版本号 {calibration_revision(document)[:16]}（任务证据会引用它）")
        return 0
    finally:
        wizard.close()


def _open_hardware(args: argparse.Namespace):
    """Open the real bus through the pinned adapter, with a clear failure message."""
    sys.path.insert(0, str(ROOT / "robot/ros2_ws/src/xlerobot_adapter"))
    try:
        from tangying_robot_gateway.calibration_hardware import XlerobotCalibrationHardware
    except ImportError:
        print("真机标定需要机器人端依赖（lerobot 与串口库）。先用 --simulate 演练流程，"
              "或在机器人上执行本脚本。", file=sys.stderr)
        raise SystemExit(2) from None
    return XlerobotCalibrationHardware(robot_id=args.robot_id)


if __name__ == "__main__":
    raise SystemExit(main())
