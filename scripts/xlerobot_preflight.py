#!/usr/bin/env python3
"""No-motion XLeRobot preflight used by robot-agent doctor robot-pi.

This script intentionally stays offline. It only checks files,
serial devices, the pinned LeRobot integration import, driver parameters and
the calibration JSON shape. Motion requires a separate operator-gated runbook.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        values[key.strip()] = value.strip()
    return values


def fail(message: str) -> int:
    print(f"FAIL {message}", file=sys.stderr)
    return 1


def pass_message(message: str) -> None:
    print(f"PASS {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config",
        type=Path,
        default=Path("/etc/tangying-robot-agent-os/robot-pi.env"),
        nargs="?",
    )
    args = parser.parse_args()

    try:
        env = read_env(args.config)
    except OSError as exc:
        return fail(f"configuration is not readable: {args.config}: {exc}")

    port1 = env.get("XLEROBOT_PORT1", "/dev/tangying-left")
    port2 = env.get("XLEROBOT_PORT2", "/dev/tangying-right")
    calibration = Path(
        env.get(
            "XLEROBOT_CALIBRATION_ROOT",
            env.get("XLEROBOT_CALIBRATION", "/var/lib/tangying-robot-agent-os/calibration"),
        )
    )
    upstream = Path(env.get("XLEROBOT_UPSTREAM_ROOT", "/opt/XLeRobot"))
    try:
        max_relative_target = float(env.get("XLEROBOT_MAX_RELATIVE_TARGET", "8.0"))
        max_action_chunk_length = int(env.get("XLEROBOT_MAX_ACTION_CHUNK_LENGTH", "64"))
    except ValueError as exc:
        return fail(f"invalid XLeRobot numeric configuration: {exc}")

    for python_path in (
        REPO_ROOT / "python",
        REPO_ROOT / "robot" / "gateway",
        REPO_ROOT / "robot" / "ros2_ws" / "src" / "xlerobot_adapter",
    ):
        if str(python_path) not in sys.path:
            sys.path.insert(0, str(python_path))

    try:
        from xlerobot_adapter.driver import XLeRobotDriver
    except ImportError as exc:
        return fail(f"cannot import XLeRobot driver: {exc}")

    driver = XLeRobotDriver(
        upstream_root=upstream,
        calibration_root=calibration,
        ports=(port1, port2),
        max_relative_target=max_relative_target,
        max_action_chunk_length=max_action_chunk_length,
    )
    capabilities = driver.capabilities()
    pass_message(
        "no-motion driver parameters "
        f"ports={port1},{port2} calibration={driver.calibration_file} "
        f"max_relative_target={max_relative_target} "
        f"max_action_chunk_length={max_action_chunk_length}"
    )
    if capabilities.blockers:
        return fail("XLeRobot driver blockers: " + ",".join(capabilities.blockers))
    pass_message("XLeRobot driver capabilities report no blockers")

    calibration_check = driver.validate_calibration_file()
    if not calibration_check.success:
        return fail(f"calibration validation failed: {calibration_check.code} {calibration_check.message}")
    pass_message(f"calibration file is parseable ({calibration_check.message})")

    print("PASS no-motion XLeRobot hardware preflight complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
