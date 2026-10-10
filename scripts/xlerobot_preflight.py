#!/usr/bin/env python3
"""No-motion XLeRobot preflight used by robot-agent doctor robot-pi.

This script intentionally stays offline. It only checks files,
serial devices, the pinned LeRobot integration import, driver parameters and
the calibration JSON shape. Motion requires a separate operator-gated runbook.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT_DEFAULT = Path("/var/lib/tangying-robot-agent-os")
EPHEMERAL_JOURNAL_ROOTS = ("/tmp", "/var/tmp", "/run", "/dev", "/private/tmp", "/private/var/tmp")


def calibration_directory(env: dict[str, str]) -> Path:
    """Match the direct Runtime and reject ambiguous ROS/direct configurations."""
    root = env.get("XLEROBOT_CALIBRATION_ROOT", str(STATE_ROOT_DEFAULT / "calibration"))
    legacy = env.get("XLEROBOT_CALIBRATION")
    if legacy and Path(legacy) != Path(root):
        raise ValueError(
            "XLEROBOT_CALIBRATION and XLEROBOT_CALIBRATION_ROOT disagree; "
            "the direct Runtime uses XLEROBOT_CALIBRATION_ROOT"
        )
    if not root.strip() or not Path(root).is_absolute():
        raise ValueError("XLEROBOT_CALIBRATION_ROOT must be an absolute directory")
    return Path(root)


def serial_device_checks(ports: tuple[str, str]) -> list[str]:
    """Inspect device metadata only; never open a port or query its hardware."""
    blockers = []
    identities = []
    for port in ports:
        try:
            info = Path(port).stat()
            if not stat.S_ISCHR(info.st_mode):
                raise ValueError("not a character device")
            if not os.access(port, os.R_OK | os.W_OK):
                raise ValueError("not readable and writable by the checking user")
            identities.append(info.st_rdev)
        except (OSError, ValueError) as exc:
            blockers.append(f"SERIAL_PORTS_UNAVAILABLE: {port}: {exc}")
    if len(identities) == 2 and identities[0] == identities[1]:
        blockers.append("SERIAL_PORTS_DUPLICATED: both ports resolve to the same device")
    return blockers


def journal_status(env: dict[str, str]) -> tuple[dict, list[str]]:
    """Read persistent safety history without creating, resetting, or locking it."""
    raw = env.get("ROBOT_RUNTIME_JOURNAL", str(STATE_ROOT_DEFAULT / "runtime-journal.json"))
    path = Path(raw)
    status = {"state": "unverified", "history_verified": False}
    blockers = []
    if not raw.strip() or not path.is_absolute():
        return status, ["ROBOT_RUNTIME_JOURNAL must be an absolute persistent file path"]
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return status, ["runtime journal path cannot be resolved safely"]
    if any(resolved.is_relative_to(Path(root).resolve()) for root in EPHEMERAL_JOURNAL_ROOTS):
        return status, ["ROBOT_RUNTIME_JOURNAL must not reside in temporary/device storage"]
    parent = resolved.parent
    if not parent.is_dir() or not os.access(parent, os.R_OK | os.W_OK | os.X_OK):
        return status, [
            "runtime journal parent must exist and be readable/writable by the checking user"
        ]
    if not resolved.exists():
        # First installation is allowed, but no history was read and no claim
        # that previously dispatched work is reconciled may follow from this.
        return {"state": "uninitialized", "history_verified": False}, []
    if not resolved.is_file() or not os.access(resolved, os.R_OK | os.W_OK):
        return status, ["runtime journal must be a readable/writable regular file"]
    try:
        from tangying_robot_gateway.journal import RuntimeJournal

        journal = RuntimeJournal(resolved)
        if journal.estop_latched:
            status["state"] = "latched_or_unresolved"
            blockers.append(
                "runtime journal safety latch is set or contains unresolved/invalid history; inspect it locally without deleting it"
            )
        else:
            status = {"state": "loaded", "history_verified": True}
    except (ImportError, OSError, ValueError) as exc:
        blockers.append(f"runtime journal could not be inspected: {type(exc).__name__}")
    return status, blockers


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
    except (OSError, UnicodeError) as exc:
        return fail(f"configuration is not readable: {args.config}: {exc}")

    port1 = env.get("XLEROBOT_PORT1", "/dev/tangying-left")
    port2 = env.get("XLEROBOT_PORT2", "/dev/tangying-right")
    try:
        calibration = calibration_directory(env)
    except ValueError as exc:
        return fail(str(exc))
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

    device_blockers = serial_device_checks((port1, port2))
    if device_blockers:
        return fail("; ".join(device_blockers))

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
        return fail(
            f"calibration validation failed: {calibration_check.code} {calibration_check.message}"
        )
    pass_message(f"calibration file is parseable ({calibration_check.message})")

    journal, journal_blockers = journal_status(env)
    if journal_blockers:
        return fail("; ".join(journal_blockers))
    pass_message(
        f"persistent journal state={journal['state']}; history_verified={journal['history_verified']}"
    )

    print("PASS no-motion XLeRobot hardware preflight complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
