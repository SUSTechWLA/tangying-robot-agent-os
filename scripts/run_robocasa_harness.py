"""Run RoboCasa process acceptance and write a machine-readable evidence pack."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

start_robocasa_handoff_stack = importlib.import_module(
    "tests.e2e.robocasa_harness"
).start_robocasa_handoff_stack

REQUIRED_ROBOT_IDS = ("robot-1", "robot-2")
VISUAL_SCREENSHOTS = (
    "overview",
    "robot-1",
    "robot-2",
    "handoff-final",
    "fallback",
)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _model_identity(world: dict) -> tuple[str, str, str | None]:
    identities = {
        (
            attributes.get("scene_id"),
            attributes.get("model_hash"),
            attributes.get("adapter"),
        )
        for entity in world.get("entities", {}).values()
        if (attributes := entity.get("attributes", {})).get("scene_id")
        or attributes.get("model_hash")
    }
    if len(identities) != 1:
        raise AssertionError(f"expected one authoritative model identity: {identities}")
    scene_id, model_hash, adapter = identities.pop()
    if not isinstance(scene_id, str) or not scene_id:
        raise AssertionError(f"invalid scene identity: {scene_id!r}")
    if not isinstance(model_hash, str) or len(model_hash) != 64:
        raise AssertionError(f"invalid model identity: {model_hash!r}")
    return scene_id, model_hash, adapter


def _canonical_joint_counts(world: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for robot_id in REQUIRED_ROBOT_IDS:
        state = world.get("robots", {}).get(robot_id, {}).get("state", {})
        joints = {
            key: value
            for key, value in state.items()
            if key.startswith("joint.") and isinstance(value, (int, float)) and math.isfinite(value)
        }
        counts[robot_id] = len(joints)
    return counts


def _canonical_joints(world: dict) -> dict[str, dict[str, float]]:
    return {
        robot_id: {
            key: value
            for key, value in world.get("robots", {}).get(robot_id, {}).get("state", {}).items()
            if key.startswith("joint.") and isinstance(value, (int, float)) and math.isfinite(value)
        }
        for robot_id in REQUIRED_ROBOT_IDS
    }


def collect_visual_evidence(stack, world: dict, output: Path) -> tuple[dict, dict]:
    scene_id, _model_hash, _adapter = _model_identity(world)
    manifest_path = f"/assets/scenes/{scene_id}/manifest.json"
    manifest = stack.public_json(manifest_path)
    manifest_url = stack.base_url + manifest_path
    base_origin = f"{urlsplit(stack.base_url).scheme}://{urlsplit(stack.base_url).netloc}"
    references = [
        manifest["sceneAsset"],
        manifest["robotModels"]["xlerobot"]["asset"],
        manifest["robotModels"]["xlerobot"]["binding"],
    ]
    requests = []
    started = time.monotonic()
    for reference in references:
        url = urljoin(manifest_url, reference)
        payload = stack.public_bytes(url)
        path = urlsplit(url).path
        filename = path.rsplit("/", 1)[-1]
        digest = hashlib.sha256(payload).hexdigest()
        expected = manifest["contentHashes"][filename]
        requests.append(
            {
                "url": url,
                "origin": f"{urlsplit(url).scheme}://{urlsplit(url).netloc}",
                "bytes": len(payload),
                "sha256": digest,
                "expectedSha256": expected,
                "hashMatches": digest == expected,
            }
        )
    asset_fetch_ms = round((time.monotonic() - started) * 1000, 3)
    external_origins = sorted(
        {item["origin"] for item in requests if item["origin"] != base_origin}
    )
    network_path = output / "visual-network.json"
    browser_capture = None
    if network_path.exists():
        existing_network = json.loads(network_path.read_text())
        browser_capture = existing_network.get("browserCapture")
        if browser_capture is None and "pageUrl" in existing_network:
            browser_capture = existing_network
    network = {
        "baseOrigin": base_origin,
        "requests": requests,
        "externalOrigins": external_origins,
        "sameOrigin": not external_origins
        and (browser_capture is None or browser_capture.get("sameOrigin") is True),
    }
    if browser_capture is not None:
        network["browserCapture"] = browser_capture
    performance_path = output / "visual-performance.json"
    if performance_path.exists():
        performance = json.loads(performance_path.read_text())
    else:
        performance = {
            "targetFirstInteractionMs": 5000,
            "targetSteadyFps": 50,
            "firstInteractionMs": None,
            "steadyFps": None,
            "browserMeasured": False,
        }
    performance["publicAssetFetchMs"] = asset_fetch_ms

    screenshot_paths = {name: f"visual/{name}.png" for name in VISUAL_SCREENSHOTS}
    write_json(output / "visual-manifest.json", manifest)
    write_json(network_path, network)
    write_json(performance_path, performance)
    return manifest, {
        "assetHashesMatch": all(item["hashMatches"] for item in requests),
        "sameOrigin": network["sameOrigin"],
        "files": {
            "manifest": "visual-manifest.json",
            "network": "visual-network.json",
            "performance": "visual-performance.json",
        },
        "screenshots": screenshot_paths,
    }


def build_acceptance_summary(
    *,
    task_id: str,
    task: dict,
    world: dict,
    manifest: dict,
    verdicts: list[dict],
    visual: dict,
) -> dict:
    scene_id, model_hash, adapter = _model_identity(world)
    robot_ids = [robot_id for robot_id in REQUIRED_ROBOT_IDS if robot_id in world.get("robots", {})]
    joint_counts = _canonical_joint_counts(world)
    placement = world.get("entities", {}).get("red-block", {}).get("relations", {}).get("inside")
    owner = world.get("resources", {}).get("block:red-block", {}).get("owner")
    harness_statuses = [item.get("status") for item in verdicts]
    checks = {
        "assetContentHashesMatch": visual["assetHashesMatch"],
        "canonicalJointCounts": joint_counts,
        "finalPlacement": placement,
        "harnessStatuses": harness_statuses,
        "modelHashMatches": manifest.get("modelHash") == model_hash,
        "resourceOwner": owner,
        "robotIds": robot_ids,
        "sameOriginAssets": visual["sameOrigin"],
        "sceneIdMatches": manifest.get("sceneId") == scene_id,
        "taskState": task.get("state"),
    }
    passed = (
        checks["assetContentHashesMatch"] is True
        and all(count >= 12 for count in joint_counts.values())
        and checks["finalPlacement"] == "right-target-zone"
        and checks["harnessStatuses"] == ["SATISFIED", "SATISFIED"]
        and checks["modelHashMatches"] is True
        and checks["resourceOwner"] == "environment"
        and checks["robotIds"] == list(REQUIRED_ROBOT_IDS)
        and checks["sameOriginAssets"] is True
        and checks["sceneIdMatches"] is True
        and checks["taskState"] == "SUCCEEDED"
    )
    return {
        "taskId": task_id,
        "state": task.get("state"),
        "passed": passed,
        "sceneId": scene_id,
        "adapter": adapter,
        "modelRevision": model_hash,
        "checks": checks,
        "visualEvidence": visual["files"],
        "screenshots": visual["screenshots"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tangying-robocasa-e2e-") as directory:
        stack = start_robocasa_handoff_stack(Path(directory))
        try:
            initial = stack.api("/v1/world")
            initial_joints = _canonical_joints(initial)
            task_id = stack.create_and_approve()
            moving = stack.wait_world(
                lambda snapshot: (
                    snapshot.get("revision", 0) > initial["revision"]
                    and any(
                        abs(value - initial_joints[robot_id].get(key, 0.0)) > 1e-3
                        for robot_id in REQUIRED_ROBOT_IDS
                        for key, value in _canonical_joints(snapshot)[robot_id].items()
                    )
                )
            )
            task = stack.wait_task(task_id)
            world = stack.wait_world(
                lambda snapshot: (
                    snapshot.get("entities", {})
                    .get("red-block", {})
                    .get("relations", {})
                    .get("inside")
                    == "right-target-zone"
                )
            )
            intents = stack.api(f"/v1/tasks/{task_id}/intents")
            events = stack.api(f"/v1/tasks/{task_id}/domain-events")
            devices = stack.api("/v1/devices")
            write_json(output / "world-initial.json", initial)
            write_json(output / "world-moving.json", moving)
            write_json(output / "world-final.json", world)
            write_json(output / "task.json", task)
            write_json(output / "intents.json", intents)
            write_json(output / "events.json", events)
            write_json(output / "devices.json", devices)
            verdicts = [
                {
                    "index": item["index"],
                    "status": item.get("harnessStatus"),
                    "reason": item.get("harnessReason"),
                    "evidenceIds": item.get("harnessEvidenceIds", []),
                }
                for item in intents["intents"]
            ]
            write_json(output / "harness-verdicts.json", verdicts)
            manifest, visual = collect_visual_evidence(stack, world, output)
            summary = build_acceptance_summary(
                task_id=task_id,
                task=task,
                world=world,
                manifest=manifest,
                verdicts=verdicts,
                visual=visual,
            )
            write_json(output / "summary.json", summary)
            if not summary["passed"]:
                raise SystemExit(1)
        finally:
            stack.stop()


if __name__ == "__main__":
    main()
