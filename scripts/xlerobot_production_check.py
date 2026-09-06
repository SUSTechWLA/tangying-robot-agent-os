#!/usr/bin/env python3
"""Offline prerequisite check for operator-reviewed XLeRobot fetch/place trials.

This script requires no-motion preflight, importable callable entity/verifier
providers, and operator-recorded hardware evidence (emergency stop, network
interruption, duplicate command and at least 30 physical trials). A pass does
not verify laptop policy inference, provider behavior, evidence authenticity,
or physical readiness. It does not invoke providers or call driver motion APIs;
configured Python modules must be trusted and safe to import without motion.
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT_DEFAULT = "/var/lib/tangying-robot-agent-os/evidence"
LIMITATIONS = [
    "Laptop policy inference and bounded action chunks are not verified by this offline check.",
    "Provider signatures, returned data and physical behavior are not exercised.",
    "Hardware and safety evidence is operator-reported; authenticity is not verified.",
    "A pass does not establish physical readiness or authorize robot motion.",
]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


def load_callable(spec: str | None):
    if not spec:
        return None
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise ValueError(f"provider must look like 'module:function', got {spec!r}")
    module = importlib.import_module(module_name)
    provider = getattr(module, attribute)
    if not callable(provider):
        raise TypeError(f"provider is not callable: {spec!r}")
    return provider


def read_evidence(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def required_evidence_checks(evidence: dict) -> list[str]:
    missing: list[str] = []
    required = {
        "emergency_stop_tested": bool,
        "network_interruption_tested": bool,
        "duplicate_command_tested": bool,
    }
    for key, value_type in required.items():
        if key not in evidence or not isinstance(evidence[key], value_type) or not evidence[key]:
            missing.append(f"evidence field {key!r} must be true")
    trials = evidence.get("completed_trials")
    if not isinstance(trials, int) or trials < 30:
        missing.append("evidence field 'completed_trials' must be an integer >= 30")
    return missing


def report_result(blockers: list[str], passes: list[str], *, json_output: bool) -> int:
    if json_output:
        print(
            json.dumps(
                {
                    "ready": not blockers,
                    "scope": "offline_prerequisites",
                    "blockers": blockers,
                    "passed": passes,
                    "limitations": LIMITATIONS,
                },
                indent=2,
            )
        )
    else:
        for message in passes:
            print(f"PASS {message}")
        for message in blockers:
            print(f"FAIL {message}")
        for message in LIMITATIONS:
            print(f"NOTE {message}")
        if blockers:
            print("NOT_READY xlerobot offline prerequisite check failed", file=sys.stderr)
        else:
            print("READY xlerobot offline prerequisites passed; physical readiness is not verified")
    return 1 if blockers else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config",
        type=Path,
        default=Path("/etc/tangying-robot-agent-os/robot-pi.env"),
        nargs="?",
    )
    parser.add_argument("--json", action="store_true", help="print result as JSON")
    args = parser.parse_args()

    for python_path in (
        REPO_ROOT / "python",
        REPO_ROOT / "robot" / "gateway",
        REPO_ROOT / "robot" / "ros2_ws" / "src" / "xlerobot_adapter",
    ):
        if str(python_path) not in sys.path:
            sys.path.insert(0, str(python_path))

    blockers: list[str] = []
    passes: list[str] = []

    try:
        env = read_env(args.config)
    except (OSError, UnicodeError) as exc:
        blockers.append(f"configuration is not readable: {args.config}: {exc}")
        return report_result(blockers, passes, json_output=args.json)

    preflight = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "xlerobot_preflight.py"),
            str(args.config),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if preflight.returncode != 0:
        blockers.append(
            "no-motion preflight failed: " + preflight.stderr.strip().replace("\n", "; ")
        )
    else:
        passes.append("no-motion preflight passed")

    providers = {
        "entity": env.get("ROBOT_ENTITY_PROVIDER", ""),
        "verifier": env.get("ROBOT_VERIFIER_PROVIDER", ""),
    }
    for name, spec in providers.items():
        if not spec:
            blockers.append(f"provider not configured: ROBOT_{name.upper()}_PROVIDER")
            continue
        try:
            # Keep provider import diagnostics out of machine-readable reports.
            with redirect_stdout(sys.stderr):
                load_callable(spec)
            passes.append(f"provider configured, importable and callable: {name}")
        except Exception as exc:  # noqa: BLE001 - readiness check must report every fault
            blockers.append(f"provider failed to load for {name}: {spec} ({exc})")

    evidence_root = Path(env.get("ROBOT_EVIDENCE_DIR", EVIDENCE_ROOT_DEFAULT))
    trial_evidence = read_evidence(evidence_root / "hardware-trials.json")
    trial_blockers = required_evidence_checks(trial_evidence)
    if trial_blockers:
        blockers.extend(f"hardware-trials.json: {message}" for message in trial_blockers)
    else:
        passes.append("hardware trials evidence >= 30 with E-stop/network/duplicate tests")

    checklist = read_evidence(evidence_root / "safety-checklist.json")
    checklist_blockers: list[str] = []
    for key in (
        "physical_estop_installed",
        "physical_estop_tested",
        "operator_present_during_trials",
    ):
        if not isinstance(checklist.get(key), bool) or not checklist[key]:
            checklist_blockers.append(f"safety-checklist.json field {key!r} must be true")
    if checklist_blockers:
        blockers.extend(checklist_blockers)
    else:
        passes.append("physical safety checklist recorded")

    return report_result(blockers, passes, json_output=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
