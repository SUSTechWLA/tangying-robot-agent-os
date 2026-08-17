#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactively calibrate the pinned XLeRobot integration. This moves hardware."
    )
    parser.add_argument("--port1", default="/dev/tangying-left")
    parser.add_argument("--port2", default="/dev/tangying-right")
    parser.add_argument(
        "--calibration-dir",
        type=Path,
        default=Path("/var/lib/tangying-robot-agent-os/calibration"),
    )
    parser.add_argument("--acknowledge-hardware-motion", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.acknowledge_hardware_motion:
        print(
            "refusing calibration: read docs/safety-checklist.md and pass "
            "--acknowledge-hardware-motion",
            file=sys.stderr,
        )
        return 2
    if not sys.stdin.isatty():
        print("refusing calibration without an interactive terminal", file=sys.stderr)
        return 2

    from lerobot.robots.xlerobot_2wheels.config_xlerobot_2wheels import (
        XLerobot2WheelsConfig,
    )
    from lerobot.robots.xlerobot_2wheels.xlerobot_2wheels import XLerobot2Wheels

    args.calibration_dir.mkdir(parents=True, exist_ok=True)
    robot = XLerobot2Wheels(
        XLerobot2WheelsConfig(
            id="tangying-xlerobot",
            port1=args.port1,
            port2=args.port2,
            calibration_dir=args.calibration_dir,
            max_relative_target=8,
        )
    )
    try:
        robot.connect(calibrate=False)
        robot.calibrate()
    finally:
        if robot.is_connected:
            robot.disconnect()
    expected = args.calibration_dir / "tangying-xlerobot.json"
    if not expected.is_file():
        print(f"calibration did not produce {expected}", file=sys.stderr)
        return 1
    print(f"calibration saved: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
