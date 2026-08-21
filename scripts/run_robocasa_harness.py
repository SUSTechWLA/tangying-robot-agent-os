"""Run RoboCasa acceptance and write a provenance-bound evidence pack."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import re
import secrets
import sys
import tempfile
import threading
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
NONCE_RE = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_VIEWPORT = (1404, 794)


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


def _load_json_list(path: Path | None) -> list | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, list) else None


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


def _custody_trajectory_valid(
    trajectory: dict | None, run_context: dict, task_id: str, intents: list[dict], final: dict
) -> bool:
    if not (
        isinstance(trajectory, dict)
        and trajectory.get("schemaVersion") == "tangying.world-trajectory.v1"
        and trajectory.get("episodeNonce") == run_context.get("episodeNonce")
        and trajectory.get("taskId") == task_id
        and isinstance(trajectory.get("samples"), list)
        and len(trajectory["samples"]) >= 4
        and _intents_valid(intents)
    ):
        return False
    samples = trajectory["samples"]
    revisions = [sample.get("revision") for sample in samples if isinstance(sample, dict)]
    projected = [_parse_timestamp(sample.get("projectedAt")) for sample in samples]
    if not (
        len(revisions) == len(samples)
        and all(type(value) is int for value in revisions)
        and all(left < right for left, right in zip(revisions, revisions[1:]))
        and all(value is not None for value in projected)
        and all(left < right for left, right in zip(projected, projected[1:]))
        and all(sample.get("acceptanceNonce") == run_context.get("episodeNonce") for sample in samples)
    ):
        return False
    observed_tokens = [
        resource.get("fencingToken")
        for sample in samples
        if isinstance((resource := sample.get("resources", {}).get("block:red-block")), dict)
    ]
    if not (
        observed_tokens
        and all(type(token) is int for token in observed_tokens)
        and all(left <= right for left, right in zip(observed_tokens, observed_tokens[1:]))
    ):
        return False
    for intent in intents:
        robot_id = intent["robotId"]
        token = intent["fencingToken"]
        if not any(
            sample.get("resources", {}).get("block:red-block", {}).get("owner") == robot_id
            and sample.get("resources", {}).get("block:red-block", {}).get("fencingToken") == token
            and sample.get("robots", {}).get(robot_id, {}).get("held") == "red-block"
            for sample in samples
        ):
            return False
    final_resource = final.get("resources", {}).get("block:red-block", {})
    return (
        final_resource.get("owner") == "environment"
        and final_resource.get("fencingToken") == intents[-1]["fencingToken"] + 1
        and samples[-1].get("revision") == final.get("revision")
        and canonical_digest(samples[-1]) == canonical_digest(final)
        and _final_held_clear(samples[-1])
    )


def _harness_evidence_valid(
    events: list | None, task_id: str, intents: list[dict], final: dict
) -> bool:
    if not isinstance(events, list) or len(intents) != 2:
        return False
    physical_events = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("eventType") in {"BLOCK_AVAILABLE", "BLOCK_DELIVERED"}
    ]
    if len(physical_events) != 2:
        return False
    expected_types = ("BLOCK_AVAILABLE", "BLOCK_DELIVERED")
    final_token = final.get("resources", {}).get("block:red-block", {}).get("fencingToken")
    for index, (intent, event, event_type) in enumerate(
        zip(intents, physical_events, expected_types, strict=True)
    ):
        payload = event.get("payload", {})
        verdict = payload.get("harness", {})
        transition = payload.get("resourceTransition", {})
        evidence_ids = intent.get("harnessEvidenceIds")
        observations = verdict.get("observations")
        if not (
            event.get("aggregateId") == task_id
            and event.get("correlationId") == task_id
            and event.get("eventType") == event_type
            and payload.get("intentIndex") == index
            and payload.get("robotId") == intent.get("robotId")
            and verdict.get("status") == intent.get("harnessStatus") == "SATISFIED"
            and verdict.get("reason") == intent.get("harnessReason")
            == "PHYSICAL_POSTCONDITIONS_SATISFIED"
            and verdict.get("evidenceIds") == evidence_ids
            and verdict.get("worldRevision") == intent.get("worldRevision")
            and isinstance(observations, list)
            and len(observations) == len(evidence_ids) == 2
            and {item.get("observationId") for item in observations} == set(evidence_ids)
            and transition.get("resourceId") == intent.get("resourceId") == "block:red-block"
            and transition.get("fromOwner") == intent.get("robotId")
            and transition.get("fromFencingToken") == intent.get("fencingToken")
            and transition.get("toOwner")
            == (intents[index + 1]["robotId"] if index == 0 else "environment")
            and transition.get("toFencingToken")
            == (intents[index + 1]["fencingToken"] if index == 0 else final_token)
        ):
            return False
        started = _parse_timestamp(intent.get("startedAt"))
        finished = _parse_timestamp(intent.get("finishedAt"))
        occurred = _parse_timestamp(event.get("occurredAt"))
        if started is None or finished is None or occurred is None or not started < finished <= occurred:
            return False
        by_source = {item.get("sourceId"): item for item in observations if isinstance(item, dict)}
        robot_observation = by_source.get(intent.get("robotSourceId"))
        entity_observations = [
            item
            for item in observations
            if isinstance(item, dict) and str(item.get("sourceId", "")).endswith("/scene")
        ]
        if len(entity_observations) != 1 or not isinstance(robot_observation, dict):
            return False
        for observation in observations:
            observed_at = _parse_timestamp(observation.get("observedAt"))
            sequence_text = observation.get("sourceSequence")
            if (
                observed_at is None
                or not started < observed_at <= finished
                or not isinstance(sequence_text, str)
                or re.fullmatch(r"[1-9][0-9]*", sequence_text) is None
            ):
                return False
        if int(robot_observation["sourceSequence"]) <= intent.get("robotSequenceBasis", -1):
            return False
        entity_observation = entity_observations[0]
        if (
            entity_observation.get("sourceId") == intent.get("entitySourceId")
            and int(entity_observation["sourceSequence"])
            <= intent.get("entitySequenceBasis", -1)
        ):
            return False
    return True


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


def _png_visual_metrics(path: Path, canvas_rect: dict | None) -> dict | None:
    try:
        from PIL import Image, ImageFilter, ImageStat

        with Image.open(path) as opened:
            image = opened.convert("RGB")
            if image.size != EXPECTED_VIEWPORT:
                return None
            sample = image.resize((176, 100))
            pixels = list(sample.get_flattened_data())
            non_black = sum(max(pixel) >= 18 for pixel in pixels) / len(pixels)
            colorful = sum(max(pixel) - min(pixel) >= 12 for pixel in pixels) / len(pixels)
            entropy = sample.convert("L").entropy()
            edges = ImageStat.Stat(sample.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]
            if not isinstance(canvas_rect, dict):
                return None
            x = int(canvas_rect.get("x", -1))
            y = int(canvas_rect.get("y", -1))
            width = int(canvas_rect.get("width", 0))
            height = int(canvas_rect.get("height", 0))
            if x < 0 or y < 0 or width < 500 or height < 300:
                return None
            if x + width > image.width or y + height > image.height:
                return None
            canvas = image.crop((x, y, x + width, y + height)).resize((136, 50))
            canvas_entropy = canvas.convert("L").entropy()
            canvas_edges = ImageStat.Stat(
                canvas.convert("L").filter(ImageFilter.FIND_EDGES)
            ).mean[0]
    except (OSError, SyntaxError, TypeError, ValueError):
        return None
    return {
        "width": EXPECTED_VIEWPORT[0],
        "height": EXPECTED_VIEWPORT[1],
        "entropy": round(entropy, 4),
        "nonBlackRatio": round(non_black, 4),
        "colorfulRatio": round(colorful, 4),
        "edgeMean": round(edges, 4),
        "canvasEntropy": round(canvas_entropy, 4),
        "canvasEdgeMean": round(canvas_edges, 4),
        "substantial": (
            entropy >= 2.5
            and non_black >= 0.35
            and colorful >= 0.04
            and edges >= 2.0
            and canvas_entropy >= 1.8
            and canvas_edges >= 1.0
        ),
    }


def _visible_rect(record: dict, viewport: tuple[int, int], *, minimum_area: int = 100) -> bool:
    if not isinstance(record, dict) or record.get("visible") is not True:
        return False
    rect = record.get("rect")
    if not isinstance(rect, dict):
        return False
    values = [rect.get(key) for key in ("x", "y", "width", "height")]
    if not all(_real_number(value) for value in values):
        return False
    x, y, width, height = values
    return (
        x >= 0
        and y >= 0
        and width > 0
        and height > 0
        and width * height >= minimum_area
        and x + width <= viewport[0]
        and y + height <= viewport[1]
    )


def _parse_timestamp(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _snapshot_order_valid(initial: dict, moving: dict, final: dict) -> bool:
    revisions = [snapshot.get("revision") for snapshot in (initial, moving, final)]
    timestamps = [_parse_timestamp(snapshot.get("projectedAt")) for snapshot in (initial, moving, final)]
    return (
        all(type(value) is int for value in revisions)
        and revisions[0] < revisions[1] < revisions[2]
        and all(value is not None for value in timestamps)
        and timestamps[0] < timestamps[1] < timestamps[2]
    )


def _browser_network_valid(network: dict | None, run_context: dict, task_id: str) -> bool:
    if network is None:
        return False
    base_url = run_context.get("publicBaseUrl")
    requests = network.get("requests")
    expected_page = base_url.rstrip("/") + f"/?acceptance_task={task_id}"
    if not isinstance(requests, list) or not requests:
        return False
    raw_urls = [item.get("url") for item in requests if isinstance(item, dict)]
    raw_valid = len(raw_urls) == len(requests) and all(
        isinstance(item.get("url"), str)
        and isinstance(item.get("responseUrl"), str)
        and item.get("method") == "GET"
        and type(item.get("status")) is int
        and 200 <= item["status"] < 400
        and item["responseUrl"] == item["url"]
        and _origin(item["url"]) == _origin(base_url)
        and _origin(item["responseUrl"]) == _origin(base_url)
        for item in requests
    )
    return (
        network.get("schemaVersion") == "tangying.browser-network.v1"
        and network.get("runId") == run_context.get("runId")
        and network.get("episodeNonce") == run_context.get("episodeNonce")
        and network.get("taskId") == task_id
        and isinstance(base_url, str)
        and network.get("pageUrl") == expected_page
        and network.get("baseOrigin")
        == f"{urlsplit(base_url).scheme}://{urlsplit(base_url).netloc}"
        and type(network.get("observedRequestCount")) is int
        and network["observedRequestCount"] == len(requests)
        and network.get("observedURLs") == raw_urls
        and raw_valid
    )


def _browser_performance_valid(performance: dict | None, run_context: dict, task_id: str) -> bool:
    if performance is None:
        return False
    interactions = performance.get("interactions", {})
    raw = performance.get("raw", {})
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
    page_started = raw.get("pageStartedAtMs")
    events = raw.get("interactionEvents")
    frame_times = raw.get("frameTimesMs")
    readiness = raw.get("readinessSamples")
    refresh_started = raw.get("refreshStartedAtMs")
    refresh_readiness = raw.get("refreshReadinessSamples")
    if not (
        _real_number(page_started)
        and isinstance(events, list)
        and isinstance(frame_times, list)
        and len(frame_times) >= 60
        and all(_real_number(value) for value in frame_times)
        and all(left < right for left, right in zip(frame_times, frame_times[1:]))
        and isinstance(readiness, list)
        and isinstance(refresh_readiness, list)
        and _real_number(refresh_started)
    ):
        return False
    raw_interactions = {
        event.get("name"): event
        for event in events
        if isinstance(event, dict) and isinstance(event.get("name"), str)
    }
    if not events or not all(
        name in raw_interactions
        and raw_interactions[name].get("passed") is True
        and _real_number(raw_interactions[name].get("atMs"))
        for name in required_interactions
    ):
        return False
    first_interaction_ms = min(event["atMs"] for event in raw_interactions.values()) - page_started
    steady_fps = (len(frame_times) - 1) * 1000 / (frame_times[-1] - frame_times[0])
    ready_at = next(
        (
            sample.get("atMs")
            for sample in readiness
            if isinstance(sample, dict)
            and sample.get("worldStatus") == "LIVE"
            and sample.get("visualStatus") == "LIVE"
            and _real_number(sample.get("atMs"))
        ),
        None,
    )
    refresh_ready_at = next(
        (
            sample.get("atMs")
            for sample in refresh_readiness
            if isinstance(sample, dict)
            and sample.get("worldStatus") == "LIVE"
            and sample.get("visualStatus") == "LIVE"
            and _real_number(sample.get("atMs"))
            and sample["atMs"] >= refresh_started
        ),
        None,
    )
    if ready_at is None or refresh_ready_at is None:
        return False
    refresh_ms = refresh_ready_at - refresh_started
    return (
        performance.get("schemaVersion") == "tangying.browser-performance.v1"
        and performance.get("runId") == run_context.get("runId")
        and performance.get("episodeNonce") == run_context.get("episodeNonce")
        and performance.get("taskId") == task_id
        and performance.get("browserMeasured") is True
        and 0 <= ready_at - page_started <= 5000
        and 0 <= first_interaction_ms <= 5000
        and abs(performance.get("firstInteractionMs", math.inf) - first_interaction_ms) < 1
        and steady_fps >= 50
        and abs(performance.get("steadyFps", math.inf) - steady_fps) < 0.2
        and 0 <= refresh_ms <= 5000
        and abs(performance.get("refreshRecoveryMs", math.inf) - refresh_ms) < 1
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
        and network.get("episodeNonce") == run_context.get("episodeNonce")
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
        and NONCE_RE.fullmatch(str(run_context.get("episodeNonce", ""))) is not None
        and browser.get("schemaVersion") == "tangying.browser-acceptance.v1"
        and browser.get("runId") == run_context.get("runId")
        and browser.get("episodeNonce") == run_context.get("episodeNonce")
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
    ) != set(VISUAL_SCREENSHOTS) or set(browser.get("captures", {})) != set(
        VISUAL_SCREENSHOTS
    ):
        return False, False, screenshot_metadata, network, performance
    for name in VISUAL_SCREENSHOTS:
        snapshot_record = browser["snapshots"][name]
        screenshot_record = browser["screenshots"][name]
        capture = browser["captures"][name]
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
        viewport = capture.get("viewport", {})
        viewport_tuple = (viewport.get("width"), viewport.get("height"))
        document = capture.get("document", {})
        marker = document.get("marker", {})
        world_badge = document.get("worldBadge", {})
        visual_badge = document.get("visualBadge", {})
        canvas = document.get("canvas", {})
        canvas_region = canvas.get("visibleRect", canvas.get("rect"))
        expected_visual = "VISUAL DEGRADED" if name == "fallback" else "VISUAL LIVE"
        expected_canvas = "fleet-godview-canvas" if name == "fallback" else "fleet-godview-webgl"
        expected_marker = (
            f"ACCEPT {run_context.get('episodeNonce')} · TASK {task_id} · REV {snapshot.get('revision') if snapshot else ''}"
        )
        dom_valid = (
            capture.get("captureName") == name
            and capture.get("episodeNonce") == run_context.get("episodeNonce")
            and capture.get("taskId") == task_id
            and capture.get("worldRevision") == (snapshot or {}).get("revision")
            and capture.get("worldDigest") == snapshot_digest
            and viewport_tuple == EXPECTED_VIEWPORT
            and document.get("acceptanceNonce") == run_context.get("episodeNonce")
            and marker.get("text") == expected_marker
            and _visible_rect(marker, EXPECTED_VIEWPORT, minimum_area=400)
            and world_badge.get("text") == "WORLD LIVE"
            and _visible_rect(world_badge, EXPECTED_VIEWPORT)
            and visual_badge.get("text") == expected_visual
            and _visible_rect(visual_badge, EXPECTED_VIEWPORT)
            and canvas.get("id") == expected_canvas
            and _visible_rect(
                {"visible": canvas.get("visible"), "rect": canvas_region},
                EXPECTED_VIEWPORT,
                minimum_area=150000,
            )
        )
        visual_metrics = (
            _png_visual_metrics(screenshot_path, canvas_region)
            if screenshot_path is not None
            else None
        )
        record_valid = (
            snapshot is not None
            and snapshot.get("schemaVersion") == "world.snapshot.v1"
            and snapshot.get("worldId") == "robocasa-handoff-v1"
            and snapshot.get("acceptanceNonce") == run_context.get("episodeNonce")
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
            and screenshot_record.get("episodeNonce") == run_context.get("episodeNonce")
            and screenshot_record.get("taskId") == task_id
            and statuses_valid
            and dom_valid
            and screenshot_path is not None
            and screenshot_path.suffix == ".png"
            and _valid_png(screenshot_path)
            and visual_metrics is not None
            and visual_metrics.get("substantial") is True
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
        screenshot_metadata[name] = {**screenshot_record, "verifiedVisualMetrics": visual_metrics}
    return provenance, screenshot_valid, screenshot_metadata, network, performance


def collect_visual_evidence(
    stack,
    world: dict,
    output: Path,
    *,
    run_id: str,
    task_id: str,
    episode_nonce: str,
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
        "episodeNonce": episode_nonce,
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
    snapshot_order = _snapshot_order_valid(initial_world, moving_world, world)
    episode_nonce = run_context.get("episodeNonce")
    nonce_valid = (
        NONCE_RE.fullmatch(str(episode_nonce or "")) is not None
        and all(
            snapshot.get("acceptanceNonce") == episode_nonce
            for snapshot in (initial_world, moving_world, world)
        )
    )
    trajectory = _load_json(output / "world-trajectory.json")
    events = _load_json_list(output / "events.json")
    custody_trajectory = _custody_trajectory_valid(
        trajectory, run_context, task_id, intents, world
    )
    harness_evidence = _harness_evidence_valid(events, task_id, intents, world)
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
        "snapshotOrder": snapshot_order,
        "episodeNonce": nonce_valid,
        "custodyTrajectory": custody_trajectory,
        "harnessEvidence": harness_evidence,
        "assetContentHashes": asset_hashes,
        "assetSameOrigin": asset_origin,
        "provenance": provenance,
        "screenshots": screenshots,
        "browserNetwork": _browser_network_valid(network, run_context, task_id),
        "browserPerformance": _browser_performance_valid(performance, run_context, task_id),
    }
    return {
        "schemaVersion": "tangying.robocasa-acceptance-summary.v3",
        "runId": run_context.get("runId"),
        "episodeNonce": episode_nonce,
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
        "world-trajectory.json",
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
    episode_nonce = secrets.token_hex(32)
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with tempfile.TemporaryDirectory(prefix="tangying-robocasa-e2e-") as directory:
        stack = start_robocasa_handoff_stack(
            Path(directory),
            human_speed=args.human_speed,
            ports=ports or None,
            episode_nonce=episode_nonce,
        )
        try:
            public_base_url = args.public_base_url.rstrip("/") or stack.base_url
            initial = stack.api("/v1/world")
            if initial.get("acceptanceNonce") != episode_nonce:
                raise AssertionError("server did not expose the runner episode nonce")
            trajectory_samples = [initial]
            trajectory_errors: list[Exception] = []
            trajectory_stop = threading.Event()

            def sample_world() -> None:
                while not trajectory_stop.wait(0.01):
                    try:
                        snapshot = stack.api("/v1/world")
                        if snapshot.get("acceptanceNonce") != episode_nonce:
                            raise AssertionError("world nonce changed during acceptance episode")
                        if snapshot.get("revision", -1) > trajectory_samples[-1].get("revision", -1):
                            trajectory_samples.append(snapshot)
                        elif snapshot.get("revision") == trajectory_samples[-1].get("revision"):
                            trajectory_samples[-1] = snapshot
                    except Exception as error:  # surfaced on the controlling thread below
                        trajectory_errors.append(error)
                        trajectory_stop.set()

            sampler = threading.Thread(target=sample_world, name="robocasa-world-evidence", daemon=True)
            sampler.start()
            initial_joints = _canonical_joints(initial)
            try:
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
            finally:
                trajectory_stop.set()
                sampler.join(timeout=5)
            if trajectory_errors:
                raise trajectory_errors[0]
            latest_sample = trajectory_samples[-1]
            if (
                latest_sample.get("revision", -1) >= world.get("revision", -1)
                and latest_sample.get("entities", {})
                .get("red-block", {})
                .get("relations", {})
                .get("inside")
                == "right-target-zone"
                and _source_freshness_valid(latest_sample)
            ):
                world = latest_sample
            if world.get("revision", -1) > trajectory_samples[-1].get("revision", -1):
                trajectory_samples.append(world)
            elif world.get("revision") == trajectory_samples[-1].get("revision"):
                trajectory_samples[-1] = world
            intent_document = stack.api(f"/v1/tasks/{task_id}/intents")
            intents = intent_document["intents"]
            write_json(output / "world-initial.json", initial)
            write_json(output / "world-moving.json", moving)
            write_json(output / "world-final.json", world)
            write_json(
                output / "world-trajectory.json",
                {
                    "schemaVersion": "tangying.world-trajectory.v1",
                    "episodeNonce": episode_nonce,
                    "taskId": task_id,
                    "samples": trajectory_samples,
                },
            )
            write_json(output / "task.json", task)
            write_json(output / "intents.json", intent_document)
            write_json(output / "events.json", stack.api(f"/v1/tasks/{task_id}/domain-events"))
            write_json(output / "devices.json", stack.api("/v1/devices"))
            write_json(output / "harness-verdicts.json", intents)
            run_context = {
                "schemaVersion": "tangying.robocasa-acceptance-run.v1",
                "runId": run_id,
                "episodeNonce": episode_nonce,
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
                episode_nonce=episode_nonce,
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
