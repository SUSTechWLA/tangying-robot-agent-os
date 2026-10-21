#!/usr/bin/env python3
"""Read-only heterogeneous coverage planning and source-bound deterministic replay."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import pathlib
import platform
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot/gateway"))
SOURCES = (
    "scripts/plan_spatial_coverage.py",
    "robot/gateway/tangying_robot_gateway/spatial_coverage.py",
    "robot/gateway/tangying_robot_gateway/spatial_navigation.py",
    "robot/gateway/tangying_robot_gateway/spatial_exploration.py",
    "robot/gateway/tangying_robot_gateway/spatial_mission.py",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_request(path):
    if path.stat().st_size > 32_000_000:
        raise ValueError("coverage request exceeds 32 MB")
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def create_plan(request_path, output):
    from tangying_robot_gateway.spatial_coverage import plan_coverage_request

    if output.exists():
        raise FileExistsError("refusing to overwrite coverage evidence: " + str(output))
    if request_path.stat().st_size > 32_000_000:
        raise ValueError("coverage request exceeds 32 MB")
    request_bytes = request_path.read_bytes()
    request = json.loads(request_bytes)
    source_bytes = {name: (ROOT / name).read_bytes() for name in SOURCES}
    plan = plan_coverage_request(request)
    if any((ROOT / name).read_bytes() != data for name, data in source_bytes.items()):
        raise RuntimeError("source changed during planning; output not published")
    output.mkdir(parents=True, exist_ok=False)
    (output / "request.json").write_bytes(request_bytes)
    write_json(output / "plan.json", plan)
    for name, data in source_bytes.items():
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    files = {str(path.relative_to(output)): {"bytes": path.stat().st_size,
             "sha256": sha(path.read_bytes())} for path in sorted(output.rglob("*")) if path.is_file()}
    write_json(output / "manifest.json", {"schemaVersion": "coverage.artifacts.v1", "state": "finalized",
               "gitHead": head.stdout.strip() if head.returncode == 0 else None,
               "files": files, "sourceHashes": {name: sha(data) for name, data in source_bytes.items()},
               "environment": {"python": platform.python_version(), "platform": platform.platform(),
                               "numpy": importlib.metadata.version("numpy")},
               "predictionOnly": True, "physicalExecution": False,
               "verification": "byte_hash_and_source_bound_deterministic_planning_replay"})
    return plan


def verify_plan(directory):
    from tangying_robot_gateway.spatial_coverage import plan_coverage_request

    manifest = json.loads((directory / "manifest.json").read_text())
    required = {"request.json", "plan.json", *("source/" + name for name in SOURCES)}
    if (manifest.get("schemaVersion") != "coverage.artifacts.v1" or manifest.get("state") != "finalized"
            or set(manifest.get("files", {})) != required
            or set(manifest.get("sourceHashes", {})) != set(SOURCES)):
        raise ValueError("coverage artifact manifest contract mismatch")
    for name, item in manifest["files"].items():
        path = directory / name
        data = path.read_bytes()
        if len(data) != item["bytes"] or sha(data) != item["sha256"]:
            raise ValueError("artifact bytes changed: " + name)
    for name in SOURCES:
        expected = manifest["sourceHashes"][name]
        if sha((ROOT / name).read_bytes()) != expected or sha((directory / "source" / name).read_bytes()) != expected:
            raise ValueError("replay requires the frozen implementation: " + name)
    stored = json.loads((directory / "plan.json").read_text())
    replanned = plan_coverage_request(read_request(directory / "request.json"))
    if replanned != stored:
        raise ValueError("deterministic planning replay disagrees with stored result")
    return {"verified": True, "predictionOnly": True, "physicalValidation": False,
            "status": stored["status"], "predictedCoverage": stored["predictedCoverage"],
            "legs": len(stored["legs"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--request", type=pathlib.Path)
    mode.add_argument("--verify", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args(argv)
    if args.verify:
        if args.output:
            parser.error("--verify does not write output")
        result = verify_plan(args.verify)
    else:
        if args.output is None:
            parser.error("--request requires a new --output directory")
        plan = create_plan(args.request, args.output)
        result = {key: plan[key] for key in ("status", "stopReason", "targetVoxels",
                  "predictedCoveredVoxels", "predictedCoverage", "observedCoverage", "physicalValidation")}
        result["output"] = str(args.output)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
