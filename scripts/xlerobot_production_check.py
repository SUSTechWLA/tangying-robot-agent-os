#!/usr/bin/env python3
"""Offline prerequisite check for operator-reviewed XLeRobot fetch/place trials.

This script requires no-motion preflight, importable callable entity/verifier
providers, and a bound Sim2Real commissioning kit (emergency stop, network
interruption, duplicate command, 30 distinct physical trials and soak). A pass does
not verify laptop policy inference, provider behavior, evidence authenticity,
or physical readiness. It does not invoke providers or call driver motion APIs;
configured Python modules must be trusted and safe to import without motion.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import sim2real
from scripts.xlerobot_preflight import calibration_directory, journal_status

LIMITATIONS = [
    "Laptop policy inference and bounded action chunks are not verified by this offline check.",
    "Provider signatures, returned data and physical behavior are not exercised.",
    "Hardware and safety evidence is operator-reported; authenticity is not verified.",
    "Sensor freshness, capture-time calibration, actual stop response, and running-process version are not live-verified.",
    "Journal inspection is a read-only snapshot; an absent journal is uninitialized, not reconciled history.",
    "Journal path checks do not prove mount durability or service-user permissions.",
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


def installed_source_identity() -> dict:
    """Identify installed source bytes; this does not identify a running process."""
    try:
        revision = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        changed = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "status",
                "--porcelain",
                "--untracked-files=normal",
                "--",
                "robot/gateway",
                "robot/ros2_ws",
                "python",
                "cmd",
                "edge",
                "internal",
                "agentruntime",
                "proto",
                "scripts/xlerobot_preflight.py",
                "scripts/xlerobot_production_check.py",
                "scripts/robot-pi-preflight.sh",
                "scripts/sim2real.py",
                "deploy/robot",
                "deploy/edge-orin",
                "go.mod",
                "go.sum",
                "pyproject.toml",
                "VERSION",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout
        return {"revision": revision, "dirty": bool(changed.strip())}
    except (OSError, subprocess.SubprocessError):
        return {"revision": None, "dirty": None}


def simulated_evidence(value) -> bool:
    """Reject explicit simulator/mock provenance, without claiming authenticity."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).lower().replace("_", "")
            if (
                normalized_key
                in {"simulated", "simulation", "simulationonly", "mock", "fake", "synthetic"}
                and item is True
            ):
                return True
            if normalized_key in {
                "adapter",
                "environment",
                "source",
                "scope",
                "mode",
                "evidenceclass",
            } and isinstance(item, str):
                source = item.strip().lower()
                if source in {
                    "sim",
                    "simulation",
                    "simulated",
                    "mock",
                    "fake",
                    "synthetic",
                    "test_fixture",
                } or source.startswith(("gazebo", "mujoco", "robocasa")):
                    return True
            if simulated_evidence(item):
                return True
    elif isinstance(value, list):
        return any(simulated_evidence(item) for item in value)
    return False


def commissioning_checks(env: dict[str, str], config: Path) -> tuple[list[str], dict]:
    """Reuse the existing artifact-bound records, never unbound trial counters."""
    blockers = []
    summary = {"live_verified": False, "physical_ready": False}
    kit_text = env.get("ROBOT_COMMISSIONING_KIT", "").strip()
    if not kit_text or not Path(kit_text).is_absolute():
        return [
            "ROBOT_COMMISSIONING_KIT must name an absolute Sim2Real kit; legacy hardware-trials.json/safety-checklist.json counters are not accepted"
        ], summary
    kit = Path(kit_text)
    try:
        report = sim2real.pilot_report(kit)
        blockers.extend(
            f"commissioning {item['code']}: {item['action']}" for item in report["blockers"]
        )
        summary.update(
            binding=report["binding"],
            evidence_counts=report["evidenceCounts"],
            stale_records=report["staleRecords"],
        )
        profile = sim2real.read_json(kit / "site.json")
        if env.get("ROBOT_ID") != profile.get("robotId"):
            blockers.append("deployed ROBOT_ID does not match the commissioning robot identity")
        # Compare without displaying configuration contents, which can contain credentials.
        if config.read_bytes() != (kit / "robot-pi.env").read_bytes():
            blockers.append(
                "deployed robot-pi.env differs from the artifact-bound commissioning configuration"
            )
        calibration = calibration_directory(env) / "tangying-xlerobot.json"
        actual_hash = hashlib.sha256(calibration.read_bytes()).hexdigest()
        if actual_hash != report["artifactHashes"].get("calibration"):
            blockers.append("deployed calibration bytes differ from the commissioned calibration")
        source = installed_source_identity()
        summary["installed_source"] = source
        if not source["revision"] or source["revision"] != profile.get("softwareRevision"):
            blockers.append(
                "installed source Git revision does not match site.json softwareRevision"
            )
        if source["dirty"] is not False:
            blockers.append(
                "installed runtime/adapter source is modified or its clean state cannot be verified"
            )
        accepted_counts = dict.fromkeys(sim2real.KINDS, 0)
        accepted_tasks = set()
        for path in sorted((kit / "evidence").glob("*.json")):
            record = sim2real.read_json(path)
            if record.get("binding") != report["binding"] or record.get("result") != "passed":
                continue
            kind = record.get("kind")
            if kind not in accepted_counts:
                continue
            if kind == "simulation":
                accepted_counts[kind] += 1
                continue
            simulated = False
            if simulated_evidence(record):
                simulated = True
                blockers.append(
                    f"commissioning record {path.name} declares simulated/mock evidence for a hardware test"
                )
            for attachment in record.get("attachments", []):
                raw = sim2real.local_file(kit, attachment["path"]).read_bytes()
                try:
                    provenance = json.loads(raw)
                except (ValueError, UnicodeError):
                    continue  # Plain logs still require an operator's authenticity review.
                if simulated_evidence(provenance):
                    simulated = True
                    blockers.append(
                        f"commissioning record {path.name} attaches simulated/mock data to a hardware test"
                    )
            if simulated:
                continue
            if kind == "trial":
                task_id = record.get("taskId")
                if not isinstance(task_id, str) or not task_id.strip():
                    blockers.append(f"commissioning trial {path.name} lacks a task ID")
                    continue
                task_id = task_id.strip()
                if task_id in accepted_tasks:
                    blockers.append(
                        f"commissioning trial {path.name} duplicates a previously recorded task ID"
                    )
                    continue
                accepted_tasks.add(task_id)
            accepted_counts[kind] += 1
        # Never increase the schema/hash/duration-validated base check's counts.
        summary["evidence_counts"] = {
            kind: min(count, report["evidenceCounts"][kind])
            for kind, count in accepted_counts.items()
        }
        for kind, count in summary["evidence_counts"].items():
            minimum = sim2real.MIN_TRIALS if kind == "trial" else 1
            if count < minimum:
                blockers.append(
                    f"commissioning accepted {kind} evidence is incomplete: {count}/{minimum}"
                )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        blockers.append(f"commissioning kit cannot be verified: {type(exc).__name__}")
    return blockers, summary


def report_result(
    blockers: list[str], passes: list[str], *, json_output: bool, details: dict | None = None
) -> int:
    if json_output:
        print(
            json.dumps(
                {
                    "ready": not blockers,
                    "scope": "offline_prerequisites",
                    "physical_ready": False,
                    "live_verified": False,
                    "blockers": blockers,
                    "passed": passes,
                    "limitations": LIMITATIONS,
                    "details": details or {},
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

    try:
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
            timeout=30,
        )
        if preflight.returncode != 0:
            blockers.append(
                "no-motion preflight failed: " + preflight.stderr.strip().replace("\n", "; ")
            )
        else:
            passes.append("no-motion preflight passed")
    except (OSError, subprocess.TimeoutExpired) as exc:
        blockers.append(f"no-motion preflight did not finish: {type(exc).__name__}")

    providers = {
        "entity": env.get("ROBOT_ENTITY_PROVIDER", ""),
        "verifier": env.get("ROBOT_VERIFIER_PROVIDER", ""),
    }
    for name, spec in providers.items():
        if not spec:
            blockers.append(f"provider not configured: ROBOT_{name.upper()}_PROVIDER")
            continue
        if any(
            part.lower() in {"sim", "simulation", "mock", "fake", "gazebo", "mujoco", "robocasa"}
            for part in spec.partition(":")[0].split(".")
        ):
            blockers.append(
                f"simulated/mock provider cannot support hardware commissioning: {name}"
            )
            continue
        try:
            # Keep provider import diagnostics out of machine-readable reports.
            with redirect_stdout(sys.stderr):
                load_callable(spec)
            passes.append(f"provider configured, importable and callable: {name}")
        except Exception as exc:  # noqa: BLE001 - readiness check must report every fault
            blockers.append(f"provider failed to load for {name}: {spec} ({exc})")

    commissioning_blockers, details = commissioning_checks(env, args.config)
    blockers.extend(commissioning_blockers)
    if not commissioning_blockers:
        passes.append(
            "current Sim2Real kit binds physical trial/stop/network/duplicate/soak records to deployed configuration, calibration and source revision"
        )
    journal, journal_blockers = journal_status(env)
    details["journal"] = journal
    blockers.extend(journal_blockers)
    return report_result(blockers, passes, json_output=args.json, details=details)


if __name__ == "__main__":
    raise SystemExit(main())
