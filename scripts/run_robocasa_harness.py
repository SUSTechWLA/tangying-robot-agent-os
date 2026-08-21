"""Run RoboCasa acceptance and write a provenance-bound evidence pack."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import re
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

start_robocasa_handoff_stack = importlib.import_module(
    "tests.e2e.robocasa_harness"
).start_robocasa_handoff_stack
HANDOFF_PROMPT = importlib.import_module("tests.e2e.fleet_harness").HANDOFF_PROMPT

REQUIRED_ROBOT_IDS = ("robot-1", "robot-2")
EXPECTED_SOURCES = {
    "robot-1/proprioception",
    "robot-1/scene",
    "robot-2/proprioception",
    "robot-2/scene",
    "coordinator/resources/block:red-block",
}
VISUAL_SCREENSHOTS = ("overview", "robot-1", "robot-2", "handoff-final", "fallback")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID_RE = re.compile(r"[0-9a-f]{32}\Z")


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def canonical_digest(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _real_number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _load_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _origin(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.netloc


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
    if not isinstance(model_hash, str) or SHA256_RE.fullmatch(model_hash) is None:
        raise AssertionError(f"invalid model identity: {model_hash!r}")
    return scene_id, model_hash, adapter


def _canonical_joints(world: dict) -> dict[str, dict[str, float]]:
    return {
        robot_id: {
            key: value
            for key, value in world.get("robots", {}).get(robot_id, {}).get("state", {}).items()
            if key.startswith("joint.") and _real_number(value)
        }
        for robot_id in REQUIRED_ROBOT_IDS
    }


def _canonical_joints_valid(world: dict) -> bool:
    robots = world.get("robots", {})
    if set(robots) != set(REQUIRED_ROBOT_IDS):
        return False
    for robot_id in REQUIRED_ROBOT_IDS:
        state = robots[robot_id].get("state", {})
        declared = {key: value for key, value in state.items() if key.startswith("joint.")}
        if len(declared) < 12 or not all(_real_number(value) for value in declared.values()):
            return False
    return True


def _joint_movement_valid(initial: dict, moving: dict) -> bool:
    if not (_canonical_joints_valid(initial) and _canonical_joints_valid(moving)):
        return False
    initial_joints = _canonical_joints(initial)
    moving_joints = _canonical_joints(moving)
    return all(
        set(initial_joints[robot_id]) == set(moving_joints[robot_id])
        and any(
            abs(value - initial_joints[robot_id][name]) > 1e-3
            for name, value in moving_joints[robot_id].items()
        )
        for robot_id in REQUIRED_ROBOT_IDS
    )


def _task_identity_valid(run_context: dict, task_id: str, task: dict) -> bool:
    return (
        isinstance(task_id, str)
        and bool(task_id)
        and run_context.get("taskId") == task_id
        and task.get("id") == task_id
        and run_context.get("request") == HANDOFF_PROMPT
        and task.get("request") == HANDOFF_PROMPT
        and run_context.get("adapter") == "robocasa"
        and task.get("adapter") == "robocasa"
        and task.get("state") == "SUCCEEDED"
    )


def _intents_valid(intents: list[dict]) -> bool:
    if not isinstance(intents, list) or len(intents) != 2:
        return False
    for expected_index, expected_robot in enumerate(REQUIRED_ROBOT_IDS):
        intent = intents[expected_index]
        evidence = intent.get("harnessEvidenceIds")
        if not (
            intent.get("index") == expected_index
            and intent.get("robotId") == expected_robot
            and intent.get("status") == "SUCCEEDED"
            and intent.get("harnessStatus") == "SATISFIED"
            and intent.get("harnessReason") == "PHYSICAL_POSTCONDITIONS_SATISFIED"
            and isinstance(evidence, list)
            and len(evidence) >= 2
            and all(isinstance(item, str) and item for item in evidence)
            and type(intent.get("fencingToken")) is int
            and intent["fencingToken"] > 0
        ):
            return False
    tokens = [intent["fencingToken"] for intent in intents]
    return len(set(tokens)) == 2 and tokens == sorted(tokens)


def _scene_identity_valid(run_context: dict, world: dict, manifest: dict) -> bool:
    try:
        scene_id, model_hash, adapter = _model_identity(world)
    except AssertionError:
        return False
    return (
        world.get("schemaVersion") == "world.snapshot.v1"
        and world.get("worldId") == "robocasa-handoff-v1"
        and run_context.get("sceneId") == "robocasa-handoff-v1"
        and scene_id == "robocasa-handoff-v1"
        and adapter == "robocasa"
        and manifest.get("sceneId") == scene_id
        and manifest.get("modelHash") == model_hash
        and SHA256_RE.fullmatch(model_hash) is not None
    )


def _final_held_clear(world: dict) -> bool:
    robots = world.get("robots", {})
    red_relations = world.get("entities", {}).get("red-block", {}).get("relations", {})
    return all(
        not robots.get(robot_id, {}).get("held") for robot_id in REQUIRED_ROBOT_IDS
    ) and not red_relations.get("held_by")


def _source_freshness_valid(world: dict) -> bool:
    sources = world.get("sources", {})
    return EXPECTED_SOURCES.issubset(sources) and all(
        source.get("freshness") == "FRESH" for source in sources.values()
    )


def _custody_valid(world: dict, intents: list[dict]) -> bool:
    resource = world.get("resources", {}).get("block:red-block", {})
    tokens = [item.get("fencingToken") for item in intents]
    return (
        _intents_valid(intents)
        and resource.get("owner") == "environment"
        and resource.get("freshness") == "FRESH"
        and type(resource.get("fencingToken")) is int
        and resource["fencingToken"] > max(tokens)
    )


def _safe_artifact(output: Path, relative: str) -> Path | None:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        return None
    resolved = (output / relative).resolve()
    try:
        resolved.relative_to(output.resolve())
    except ValueError:
        return None
    return resolved


def _valid_png(path: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as image:
            if image.format != "PNG" or image.width < 1 or image.height < 1:
                return False
            image.verify()
        with Image.open(path) as image:
            image.load()
    except (OSError, SyntaxError, ValueError):
        return False
    return True


def _browser_network_valid(network: dict | None, run_context: dict, task_id: str) -> bool:
    if network is None:
        return False
    base_url = run_context.get("publicBaseUrl")
    urls = network.get("observedURLs")
    return (
        network.get("schemaVersion") == "tangying.browser-network.v1"
        and network.get("runId") == run_context.get("runId")
        and network.get("taskId") == task_id
        and isinstance(base_url, str)
        and network.get("pageUrl") == base_url.rstrip("/") + "/"
        and network.get("baseOrigin")
        == f"{urlsplit(base_url).scheme}://{urlsplit(base_url).netloc}"
        and type(network.get("observedRequestCount")) is int
        and isinstance(urls, list)
        and network["observedRequestCount"] == len(urls)
        and len(urls) > 0
        and all(isinstance(url, str) and _origin(url) == _origin(base_url) for url in urls)
        and network.get("externalOrigins") == []
        and network.get("sameOrigin") is True
    )


def _browser_performance_valid(performance: dict | None, run_context: dict, task_id: str) -> bool:
    if performance is None:
        return False
    interactions = performance.get("interactions", {})
    required_interactions = (
        "fourIndependentToggles",
        "leftPanChangedFrame",
        "pointerZoomChangedFrame",
        "resetChangedFrame",
        "topPresetChangedFrame",
        "followEnabled",
        "panCancelledFollow",
        "selectedAndFocusedRobot2",
        "refreshRestoredCamera",
        "cachedOfflineCameraInteractive",
    )
    return (
        performance.get("schemaVersion") == "tangying.browser-performance.v1"
        and performance.get("runId") == run_context.get("runId")
        and performance.get("taskId") == task_id
        and performance.get("browserMeasured") is True
        and _real_number(performance.get("firstInteractionMs"))
        and 0 <= performance["firstInteractionMs"] <= 5000
        and _real_number(performance.get("steadyFps"))
        and performance["steadyFps"] >= 50
        and _real_number(performance.get("refreshRecoveryMs"))
        and 0 <= performance["refreshRecoveryMs"] <= 5000
        and all(interactions.get(name) is True for name in required_interactions)
        and "deterministic" in str(interactions.get("rightOrbit", ""))
        and "blocked" in str(interactions.get("fileMode", ""))
    )


def _asset_evidence_valid(
    output: Path,
    run_context: dict,
    task_id: str,
    manifest: dict,
    visual: dict,
) -> tuple[bool, bool]:
    saved_manifest = _load_json(output / "visual-manifest.json")
    network = _load_json(output / "visual-asset-network.json")
    base_url = run_context.get("publicBaseUrl")
    if saved_manifest != manifest or network is None or not isinstance(base_url, str):
        return False, False
    base_origin = f"{urlsplit(base_url).scheme}://{urlsplit(base_url).netloc}"
    manifest_url = base_url.rstrip("/") + "/assets/scenes/robocasa-handoff-v1/manifest.json"
    try:
        references = [
            manifest["sceneAsset"],
            manifest["robotModels"]["xlerobot"]["asset"],
            manifest["robotModels"]["xlerobot"]["binding"],
        ]
        expected_urls = [urljoin(manifest_url, reference) for reference in references]
        content_hashes = manifest["contentHashes"]
    except (KeyError, TypeError):
        return False, False
    requests = network.get("requests")
    provenance = (
        network.get("schemaVersion") == "tangying.asset-network.v1"
        and network.get("runId") == run_context.get("runId")
        and network.get("taskId") == task_id
        and network.get("baseOrigin") == base_origin
        and isinstance(requests, list)
        and len(requests) == len(expected_urls)
    )
    hashes_valid = provenance and visual.get("assetHashesMatch") is True
    origins_valid = provenance and visual.get("sameOrigin") is True
    if not isinstance(requests, list) or len(requests) != len(expected_urls):
        return False, False
    for item, expected_url in zip(requests, expected_urls, strict=True):
        filename = urlsplit(expected_url).path.rsplit("/", 1)[-1]
        expected_hash = content_hashes.get(filename)
        item_hash = item.get("sha256")
        item_origin = item.get("origin")
        origins_valid = origins_valid and (
            _origin(expected_url) == _origin(base_url)
            and item.get("url") == expected_url
            and item_origin == base_origin
            and _origin(str(item.get("url", ""))) == _origin(base_url)
        )
        hashes_valid = hashes_valid and (
            isinstance(expected_hash, str)
            and SHA256_RE.fullmatch(expected_hash) is not None
            and isinstance(item_hash, str)
            and SHA256_RE.fullmatch(item_hash) is not None
            and item_hash == expected_hash
            and item.get("expectedSha256") == expected_hash
            and item.get("hashMatches") is True
            and type(item.get("bytes")) is int
            and item["bytes"] > 0
        )
    origins_valid = origins_valid and (
        network.get("externalOrigins") == [] and network.get("sameOrigin") is True
    )
    return bool(hashes_valid), bool(origins_valid)


def _provenance_and_screenshots(
    output: Path,
    run_context: dict,
    task_id: str,
    initial_world: dict,
    moving_world: dict,
    world: dict,
) -> tuple[bool, bool, dict, dict | None, dict | None]:
    browser = _load_json(output / "browser-evidence.json")
    network = _load_json(output / "visual-network.json")
    performance = _load_json(output / "visual-performance.json")
    run_file = _load_json(output / "run-context.json")
    provenance = (
        browser is not None
        and run_file == run_context
        and RUN_ID_RE.fullmatch(str(run_context.get("runId", ""))) is not None
        and browser.get("schemaVersion") == "tangying.browser-acceptance.v1"
        and browser.get("runId") == run_context.get("runId")
        and browser.get("taskId") == task_id
        and browser.get("request") == HANDOFF_PROMPT
        and browser.get("adapter") == "robocasa"
        and browser.get("sceneId") == "robocasa-handoff-v1"
        and browser.get("contextDigest") == canonical_digest(run_context)
    )
    expected_worlds = {"initial": initial_world, "moving": moving_world, "final": world}
    for name, snapshot in expected_worlds.items():
        record = run_context.get("snapshots", {}).get(name, {})
        provenance = provenance and (
            record.get("revision") == snapshot.get("revision")
            and record.get("projectedAt") == snapshot.get("projectedAt")
            and record.get("digest") == canonical_digest(snapshot)
        )
    screenshot_valid = provenance
    screenshot_metadata: dict[str, dict] = {}
    if browser is None:
        return False, False, screenshot_metadata, network, performance
    if set(browser.get("snapshots", {})) != set(VISUAL_SCREENSHOTS) or set(
        browser.get("screenshots", {})
    ) != set(VISUAL_SCREENSHOTS):
        return False, False, screenshot_metadata, network, performance
    for name in VISUAL_SCREENSHOTS:
        snapshot_record = browser["snapshots"][name]
        screenshot_record = browser["screenshots"][name]
        snapshot_path = _safe_artifact(output, snapshot_record.get("path"))
        screenshot_path = _safe_artifact(output, screenshot_record.get("path"))
        snapshot = _load_json(snapshot_path)
        snapshot_digest = canonical_digest(snapshot) if snapshot is not None else ""
        try:
            screenshot_payload = (
                screenshot_path.read_bytes() if screenshot_path is not None else b""
            )
        except OSError:
            screenshot_payload = b""
        statuses_valid = screenshot_record.get("worldStatus") == "LIVE" and screenshot_record.get(
            "visualStatus"
        ) == ("DEGRADED" if name == "fallback" else "LIVE")
        record_valid = (
            snapshot is not None
            and snapshot.get("schemaVersion") == "world.snapshot.v1"
            and snapshot.get("worldId") == "robocasa-handoff-v1"
            and type(snapshot.get("revision")) is int
            and snapshot["revision"] >= world.get("revision", -1)
            and snapshot_record.get("revision") == snapshot["revision"]
            and snapshot_record.get("projectedAt") == snapshot.get("projectedAt")
            and snapshot_record.get("digest") == snapshot_digest
            and screenshot_record.get("path") == f"visual/{name}.png"
            and screenshot_record.get("format") == "png"
            and screenshot_record.get("sha256") == hashlib.sha256(screenshot_payload).hexdigest()
            and screenshot_record.get("bytes") == len(screenshot_payload)
            and screenshot_record.get("worldRevision") == snapshot["revision"]
            and screenshot_record.get("worldDigest") == snapshot_digest
            and statuses_valid
            and screenshot_path is not None
            and screenshot_path.suffix == ".png"
            and _valid_png(screenshot_path)
        )
        if name in {"handoff-final", "fallback"}:
            record_valid = record_valid and (
                snapshot.get("entities", {}).get("red-block", {}).get("relations", {}).get("inside")
                == "right-target-zone"
                and _final_held_clear(snapshot)
                and _source_freshness_valid(snapshot)
            )
        provenance = provenance and record_valid
        screenshot_valid = screenshot_valid and record_valid
        screenshot_metadata[name] = screenshot_record
    return provenance, screenshot_valid, screenshot_metadata, network, performance


def collect_visual_evidence(
    stack,
    world: dict,
    output: Path,
    *,
    run_id: str,
    task_id: str,
    public_base_url: str | None = None,
) -> tuple[dict, dict]:
    scene_id, _model_hash, _adapter = _model_identity(world)
    base_url = (public_base_url or stack.base_url).rstrip("/")
    manifest_path = f"/assets/scenes/{scene_id}/manifest.json"
    manifest_url = base_url + manifest_path
    manifest = (
        stack.public_json(manifest_path)
        if public_base_url is None
        else json.loads(stack.public_bytes(manifest_url, base_url=base_url))
    )
    base_origin = f"{urlsplit(base_url).scheme}://{urlsplit(base_url).netloc}"
    references = [
        manifest["sceneAsset"],
        manifest["robotModels"]["xlerobot"]["asset"],
        manifest["robotModels"]["xlerobot"]["binding"],
    ]
    urls = [urljoin(manifest_url, reference) for reference in references]
    if any(_origin(url) != _origin(base_url) for url in urls):
        raise AssertionError("visual asset reference origin mismatch")
    requests = []
    started = time.monotonic()
    for url in urls:
        payload = stack.public_bytes(url, base_url=base_url)
        filename = urlsplit(url).path.rsplit("/", 1)[-1]
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
    asset_network = {
        "schemaVersion": "tangying.asset-network.v1",
        "runId": run_id,
        "taskId": task_id,
        "baseOrigin": base_origin,
        "requests": requests,
        "externalOrigins": [],
        "sameOrigin": all(item["origin"] == base_origin for item in requests),
        "publicAssetFetchMs": round((time.monotonic() - started) * 1000, 3),
    }
    write_json(output / "visual-manifest.json", manifest)
    write_json(output / "visual-asset-network.json", asset_network)
    return manifest, {
        "assetHashesMatch": all(item["hashMatches"] for item in requests),
        "sameOrigin": asset_network["sameOrigin"],
        "files": {
            "manifest": "visual-manifest.json",
            "assetNetwork": "visual-asset-network.json",
            "network": "visual-network.json",
            "performance": "visual-performance.json",
            "browser": "browser-evidence.json",
        },
    }


def build_acceptance_summary(
    *,
    output: Path,
    run_context: dict,
    task_id: str,
    task: dict,
    initial_world: dict,
    moving_world: dict,
    world: dict,
    manifest: dict,
    intents: list[dict],
    visual: dict,
) -> dict:
    scene_valid = _scene_identity_valid(run_context, world, manifest)
    canonical_valid = _canonical_joints_valid(world)
    movement_valid = _joint_movement_valid(initial_world, moving_world)
    task_valid = _task_identity_valid(run_context, task_id, task)
    intents_valid = _intents_valid(intents)
    final_placement = (
        world.get("entities", {}).get("red-block", {}).get("relations", {}).get("inside")
        == "right-target-zone"
    )
    held_valid = _final_held_clear(world)
    sources_valid = _source_freshness_valid(world)
    custody_valid = _custody_valid(world, intents)
    asset_hashes, asset_origin = _asset_evidence_valid(
        output, run_context, task_id, manifest, visual
    )
    provenance, screenshots, screenshot_metadata, network, performance = (
        _provenance_and_screenshots(
            output, run_context, task_id, initial_world, moving_world, world
        )
    )
    checks = {
        "taskIdentity": task_valid,
        "sceneIdentity": scene_valid,
        "modelIdentity": scene_valid,
        "canonicalJoints": canonical_valid,
        "jointMovement": movement_valid,
        "intents": intents_valid,
        "finalPlacement": final_placement,
        "finalHeldClear": held_valid,
        "sourceFreshness": sources_valid,
        "custody": custody_valid,
        "assetContentHashes": asset_hashes,
        "assetSameOrigin": asset_origin,
        "provenance": provenance,
        "screenshots": screenshots,
        "browserNetwork": _browser_network_valid(network, run_context, task_id),
        "browserPerformance": _browser_performance_valid(performance, run_context, task_id),
    }
    return {
        "schemaVersion": "tangying.robocasa-acceptance-summary.v2",
        "runId": run_context.get("runId"),
        "taskId": task_id,
        "state": task.get("state"),
        "passed": all(value is True for value in checks.values()),
        "sceneId": run_context.get("sceneId"),
        "modelRevision": manifest.get("modelHash"),
        "checks": checks,
        "snapshotDigests": run_context.get("snapshots"),
        "visualEvidence": visual.get("files", {}),
        "screenshots": screenshot_metadata,
    }


def _snapshot_record(world: dict) -> dict:
    return {
        "revision": world.get("revision"),
        "projectedAt": world.get("projectedAt"),
        "digest": canonical_digest(world),
    }


def _purge_previous_evidence(output: Path) -> None:
    for name in (
        "summary.json",
        "run-context.json",
        "browser-evidence.json",
        "visual-network.json",
        "visual-performance.json",
        "visual-manifest.json",
        "visual-asset-network.json",
        "world-initial.json",
        "world-moving.json",
        "world-final.json",
        "task.json",
        "intents.json",
        "events.json",
        "devices.json",
        "harness-verdicts.json",
    ):
        path = output / name
        if path.exists():
            path.unlink()
    visual = output / "visual"
    if visual.exists():
        for path in visual.iterdir():
            if path.is_file():
                path.unlink()


def _wait_for_browser_evidence(output: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (output / "browser-evidence.json").exists():
            return
        time.sleep(0.1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ports", default="")
    parser.add_argument("--public-base-url", default="")
    parser.add_argument("--browser-evidence-timeout", type=float, default=0)
    parser.add_argument("--human-speed", type=float, default=0.02)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    _purge_previous_evidence(output)
    ports = tuple(int(value) for value in args.ports.split(",") if value)
    if ports and len(ports) != 4:
        raise SystemExit("--ports requires fleet,gateway,runtime1,runtime2")
    run_id = uuid.uuid4().hex
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with tempfile.TemporaryDirectory(prefix="tangying-robocasa-e2e-") as directory:
        stack = start_robocasa_handoff_stack(
            Path(directory), human_speed=args.human_speed, ports=ports or None
        )
        try:
            public_base_url = args.public_base_url.rstrip("/") or stack.base_url
            initial = stack.api("/v1/world")
            initial_joints = _canonical_joints(initial)
            task_id = stack.create_and_approve(HANDOFF_PROMPT)
            moving = stack.wait_world(
                lambda snapshot: (
                    snapshot.get("revision", 0) > initial["revision"]
                    and all(
                        any(
                            abs(value - initial_joints[robot_id].get(key, value)) > 1e-3
                            for key, value in _canonical_joints(snapshot)[robot_id].items()
                        )
                        for robot_id in REQUIRED_ROBOT_IDS
                    )
                ),
                timeout=120,
            )
            task = stack.wait_task(task_id)
            world = stack.wait_world(
                lambda snapshot: (
                    snapshot.get("entities", {})
                    .get("red-block", {})
                    .get("relations", {})
                    .get("inside")
                    == "right-target-zone"
                    and _source_freshness_valid(snapshot)
                )
            )
            intent_document = stack.api(f"/v1/tasks/{task_id}/intents")
            intents = intent_document["intents"]
            write_json(output / "world-initial.json", initial)
            write_json(output / "world-moving.json", moving)
            write_json(output / "world-final.json", world)
            write_json(output / "task.json", task)
            write_json(output / "intents.json", intent_document)
            write_json(output / "events.json", stack.api(f"/v1/tasks/{task_id}/domain-events"))
            write_json(output / "devices.json", stack.api("/v1/devices"))
            write_json(output / "harness-verdicts.json", intents)
            run_context = {
                "schemaVersion": "tangying.robocasa-acceptance-run.v1",
                "runId": run_id,
                "taskId": task_id,
                "request": HANDOFF_PROMPT,
                "adapter": "robocasa",
                "sceneId": "robocasa-handoff-v1",
                "publicBaseUrl": public_base_url,
                "startedAt": started_at,
                "snapshots": {
                    "initial": _snapshot_record(initial),
                    "moving": _snapshot_record(moving),
                    "final": _snapshot_record(world),
                },
            }
            write_json(output / "run-context.json", run_context)
            manifest, visual = collect_visual_evidence(
                stack,
                world,
                output,
                run_id=run_id,
                task_id=task_id,
                public_base_url=public_base_url if public_base_url != stack.base_url else None,
            )
            _wait_for_browser_evidence(output, args.browser_evidence_timeout)
            summary = build_acceptance_summary(
                output=output,
                run_context=run_context,
                task_id=task_id,
                task=task,
                initial_world=initial,
                moving_world=moving,
                world=world,
                manifest=manifest,
                intents=intents,
                visual=visual,
            )
            write_json(output / "summary.json", summary)
            if not summary["passed"]:
                raise SystemExit(1)
        finally:
            stack.stop()


if __name__ == "__main__":
    main()
