"""Acceptance for the served RoboCasa visual twin and live joint state."""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import math
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

import pytest
from PIL import Image, ImageDraw

from tests.e2e.fleet_harness import HANDOFF_PROMPT

if TYPE_CHECKING:
    from tests.e2e.robocasa_harness import RoboCasaHandoffStack

pytest_plugins = ("tests.e2e.robocasa_harness",)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
RUN_ID = "0123456789abcdef0123456789abcdef"
EPISODE_NONCE = "fedcba9876543210" * 4
TASK_ID = "task-review-1"
MODEL_HASH = "a" * 64
VIEWPORT = (1404, 794)


def _substantial_png(*, black: bool = False, size: tuple[int, int] = VIEWPORT) -> bytes:
    image = Image.new("RGB", size, "black" if black else "#18304a")
    if not black:
        draw = ImageDraw.Draw(image)
        for x in range(0, size[0], 16):
            color = ((x * 13) % 255, (x * 29 + 80) % 255, (x * 47 + 160) % 255)
            draw.rectangle((x, 0, min(size[0], x + 8), size[1]), fill=color)
        for y in range(0, size[1], 19):
            draw.line((0, y, size[0], size[1] - y), fill=(245, 225, y % 255), width=3)
    payload = io.BytesIO()
    image.save(payload, "PNG")
    return payload.getvalue()


GOOD_PNG = _substantial_png()


def _digest(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _world(
    revision: int,
    *,
    moved: bool = False,
    final: bool = False,
    owner: str | None = None,
    token: int | None = None,
    held_by: str | None = None,
    evidence_suffix: str = "base",
) -> dict:
    observed_at = f"2026-08-22T00:00:{revision:02d}Z"
    observed_nanos = 1787356800000000000 + revision * 1_000_000_000
    entity_source = f"{held_by or 'robot-2'}/scene"
    robot_1 = {f"joint.arm.{index}": float(index) for index in range(12)}
    robot_2 = {f"joint.arm.{index}": float(index) for index in range(12)}
    if moved:
        robot_1["joint.arm.0"] = 0.5
        robot_2["joint.arm.0"] = -0.5
    sources = {
        source_id: {"sourceId": source_id, "freshness": "FRESH"}
        for source_id in (
            "robot-1/proprioception",
            "robot-1/scene",
            "robot-2/proprioception",
            "robot-2/scene",
            "coordinator/resources/block:red-block",
        )
    }
    return {
        "schemaVersion": "world.snapshot.v1",
        "worldId": "robocasa-handoff-v1",
        "revision": revision,
        "projectedAt": f"2026-08-22T00:00:{revision:02d}Z",
        "acceptanceNonce": EPISODE_NONCE,
        "entities": {
            "robot-1": {
                "entityId": "robot-1",
                "attributes": {
                    "scene_id": "robocasa-handoff-v1",
                    "model_hash": MODEL_HASH,
                    "adapter": "robocasa",
                },
            },
            "robot-2": {
                "entityId": "robot-2",
                "attributes": {
                    "scene_id": "robocasa-handoff-v1",
                    "model_hash": MODEL_HASH,
                    "adapter": "robocasa",
                },
            },
            "red-block": {
                "entityId": "red-block",
                "relations": {"inside": "right-target-zone" if final else "left-start-zone"},
                "freshness": "FRESH",
                "evidence": {
                    "observationId": f"{entity_source}/{revision + 10}/{observed_nanos}",
                    "sourceId": entity_source,
                    "sourceSequence": revision + 10,
                    "observedAt": observed_at,
                    "frameId": "world",
                    "transformRevision": "robocasa-world-v1",
                },
            },
        },
        "robots": {
            "robot-1": {
                "robotId": "robot-1",
                "state": robot_1,
                "freshness": "FRESH",
                "activity": "IDLE",
                "held": "red-block" if held_by == "robot-1" else "",
                "evidence": {
                    "observationId": f"robot-1/proprioception/{revision + 20}/{observed_nanos}",
                    "sourceId": "robot-1/proprioception",
                    "sourceSequence": revision + 20,
                    "observedAt": observed_at,
                    "frameId": "world",
                    "transformRevision": "robocasa-world-v1",
                },
            },
            "robot-2": {
                "robotId": "robot-2",
                "state": robot_2,
                "freshness": "FRESH",
                "activity": "IDLE",
                "held": "red-block" if held_by == "robot-2" else "",
                "evidence": {
                    "observationId": f"robot-2/proprioception/{revision + 30}/{observed_nanos}",
                    "sourceId": "robot-2/proprioception",
                    "sourceSequence": revision + 30,
                    "observedAt": observed_at,
                    "frameId": "world",
                    "transformRevision": "robocasa-world-v1",
                },
            },
        },
        "resources": (
            {
                "block:red-block": {
                    "resourceId": "block:red-block",
                    "owner": owner or "environment",
                    "fencingToken": token if token is not None else 3,
                    "freshness": "FRESH",
                }
            }
            if owner is not None or final
            else {}
        ),
        "sources": sources,
    }


def _write_browser_evidence(tmp_path, run_context: dict, snapshot: dict) -> None:
    visual_dir = tmp_path / "visual"
    visual_dir.mkdir(exist_ok=True)
    snapshots = {}
    screenshots = {}
    for name in ("overview", "robot-1", "robot-2", "handoff-final", "fallback"):
        snapshot_path = visual_dir / f"world-{name}.json"
        snapshot_path.write_text(json.dumps(snapshot))
        digest = _digest(snapshot)
        screenshot_path = visual_dir / f"{name}.png"
        screenshot_path.write_bytes(GOOD_PNG)
        snapshots[name] = {
            "path": f"visual/world-{name}.json",
            "revision": snapshot["revision"],
            "projectedAt": snapshot["projectedAt"],
            "digest": digest,
        }
        screenshots[name] = {
            "path": f"visual/{name}.png",
            "format": "png",
            "sha256": hashlib.sha256(GOOD_PNG).hexdigest(),
            "bytes": len(GOOD_PNG),
            "capturedAt": "2026-08-22T00:01:00Z",
            "worldRevision": snapshot["revision"],
            "worldDigest": digest,
            "worldStatus": "LIVE",
            "visualStatus": "DEGRADED" if name == "fallback" else "LIVE",
            "episodeNonce": EPISODE_NONCE,
            "taskId": run_context["taskId"],
        }
    captures = {}
    for name in ("overview", "robot-1", "robot-2", "handoff-final", "fallback"):
        visual_status = "DEGRADED" if name == "fallback" else "LIVE"
        canvas_id = "fleet-godview-canvas" if name == "fallback" else "fleet-godview-webgl"
        captures[name] = {
            "captureName": name,
            "capturedAt": "2026-08-22T00:01:00Z",
            "episodeNonce": EPISODE_NONCE,
            "taskId": run_context["taskId"],
            "worldRevision": snapshot["revision"],
            "worldDigest": _digest(snapshot),
            "viewport": {"width": VIEWPORT[0], "height": VIEWPORT[1]},
            "document": {
                "acceptanceNonce": EPISODE_NONCE,
                "marker": {
                    "text": f"ACCEPT {EPISODE_NONCE} · TASK {run_context['taskId']} · REV {snapshot['revision']}",
                    "visible": True,
                    "rect": {"x": 20, "y": 750, "width": 900, "height": 28},
                },
                "worldBadge": {
                    "text": "WORLD LIVE",
                    "visible": True,
                    "rect": {"x": 1000, "y": 190, "width": 110, "height": 24},
                },
                "visualBadge": {
                    "text": f"VISUAL {visual_status}",
                    "visible": True,
                    "rect": {"x": 1120, "y": 190, "width": 150, "height": 24},
                },
                "canvas": {
                    "id": canvas_id,
                    "visible": True,
                    "rect": {"x": 20, "y": 220, "width": 1360, "height": 500},
                },
            },
        }
    browser = {
        "schemaVersion": "tangying.browser-acceptance.v1",
        "runId": run_context["runId"],
        "episodeNonce": EPISODE_NONCE,
        "taskId": run_context["taskId"],
        "request": HANDOFF_PROMPT,
        "adapter": "robocasa",
        "sceneId": "robocasa-handoff-v1",
        "contextDigest": _digest(run_context),
        "receiverAuthenticated": True,
        "capturedAt": "2026-08-22T00:01:00Z",
        "snapshots": snapshots,
        "screenshots": screenshots,
        "captures": captures,
        "networkFile": "visual-network.json",
        "performanceFile": "visual-performance.json",
    }
    (tmp_path / "browser-evidence.json").write_text(json.dumps(browser))
    (tmp_path / "visual-network.json").write_text(
        json.dumps(
            {
                "schemaVersion": "tangying.browser-network.v1",
                "runId": run_context["runId"],
                "taskId": run_context["taskId"],
                "capturedAt": "2026-08-22T00:01:00Z",
                "episodeNonce": EPISODE_NONCE,
                "pageUrl": f"http://127.0.0.1:18080/?acceptance_task={run_context['taskId']}",
                "baseOrigin": "http://127.0.0.1:18080",
                "observedRequestCount": 5,
                "observedURLs": [
                    f"http://127.0.0.1:18080/?acceptance_task={run_context['taskId']}",
                    "http://127.0.0.1:18080/assets/scenes/robocasa-handoff-v1/manifest.json",
                    "http://127.0.0.1:18080/assets/scenes/robocasa-handoff-v1/scene.glb",
                    "http://127.0.0.1:18080/assets/scenes/robocasa-handoff-v1/xlerobot.glb",
                    "http://127.0.0.1:18080/assets/scenes/robocasa-handoff-v1/xlerobot.binding.json",
                ],
                "externalOrigins": [],
                "sameOrigin": True,
                "cacheDisabled": True,
                "requests": [
                    {
                        "role": role,
                        "url": url,
                        "method": "GET",
                        "status": 200,
                        "responseUrl": url,
                        "requestHeaders": {"Cache-Control": "no-cache, no-store"},
                        "responseHeaders": {"X-Tangying-Acceptance-Nonce": EPISODE_NONCE},
                        "bytes": 100,
                        "sha256": "e" * 64,
                    }
                    for role, url in zip(
                        ("document", "manifest", "scene", "robot", "binding"),
                        [
                            f"http://127.0.0.1:18080/?acceptance_task={run_context['taskId']}",
                            "http://127.0.0.1:18080/assets/scenes/robocasa-handoff-v1/manifest.json",
                            "http://127.0.0.1:18080/assets/scenes/robocasa-handoff-v1/scene.glb",
                            "http://127.0.0.1:18080/assets/scenes/robocasa-handoff-v1/xlerobot.glb",
                            "http://127.0.0.1:18080/assets/scenes/robocasa-handoff-v1/xlerobot.binding.json",
                        ],
                        strict=True,
                    )
                ],
            }
        )
    )
    (tmp_path / "visual-performance.json").write_text(
        json.dumps(
            {
                "schemaVersion": "tangying.browser-performance.v1",
                "runId": run_context["runId"],
                "episodeNonce": EPISODE_NONCE,
                "taskId": run_context["taskId"],
                "capturedAt": "2026-08-22T00:01:00Z",
                "browserMeasured": True,
                "firstInteractionMs": 500,
                "steadyFps": 60.0,
                "renderCapacityFps": 1000 / 5.4,
                "renderDurationMeanMs": 5.4,
                "renderDurationMedianMs": 5.4,
                "renderDurationP90Ms": 5.8,
                "renderDurationP95Ms": 5.8,
                "renderDurationMaxMs": 5.8,
                "renderSteadySampleCount": 180,
                "renderSteadyWindowStartedAtMs": 3000.0,
                "renderSteadyWindowEndedAtMs": 6580.044,
                "refreshRecoveryMs": 600,
                "interactions": {
                    "fourIndependentToggles": True,
                    "leftPanChangedFrame": True,
                    "pointerZoomChangedFrame": True,
                    "resetChangedFrame": True,
                    "topPresetChangedFrame": True,
                    "followEnabled": True,
                    "panCancelledFollow": True,
                    "selectedAndFocusedRobot2": True,
                    "refreshRestoredCamera": True,
                    "cachedOfflineCameraInteractive": True,
                    "rightOrbit": "covered by deterministic browser interaction test; browser-client CUA drag has no right-button parameter",
                    "fileMode": "blocked by in-app browser URL policy; automated app test verifies zero requests and service link",
                },
                "raw": {
                    "pageStartedAtMs": 1000.0,
                    "interactionEvents": [
                        {"name": "fourIndependentToggles", "atMs": 1500.0, "passed": True},
                        {"name": "leftPanChangedFrame", "atMs": 1510.0, "passed": True},
                        {"name": "pointerZoomChangedFrame", "atMs": 1520.0, "passed": True},
                        {"name": "resetChangedFrame", "atMs": 1530.0, "passed": True},
                        {"name": "topPresetChangedFrame", "atMs": 1540.0, "passed": True},
                        {"name": "followEnabled", "atMs": 1550.0, "passed": True},
                        {"name": "panCancelledFollow", "atMs": 1560.0, "passed": True},
                        {"name": "selectedAndFocusedRobot2", "atMs": 1570.0, "passed": True},
                        {"name": "refreshRestoredCamera", "atMs": 1580.0, "passed": True},
                        {"name": "cachedOfflineCameraInteractive", "atMs": 1590.0, "passed": True},
                    ],
                    "frameTimesMs": [
                        1000.0 + index * (1000.0 / 60.0) + (index % 7) * 0.017
                        for index in range(600)
                    ],
                    "renderDurationMs": [
                        5.0 + (index % 9) * 0.1 for index in range(180)
                    ],
                    "renderDurationTimestampsMs": [
                        3000.0 + index * 20.0 + (index % 7) * 0.011 for index in range(180)
                    ],
                    "pageTimeOriginMs": 0.0,
                    "readinessSamples": [
                        {"atMs": 1100.0, "worldStatus": "CONNECTING", "visualStatus": "LOADING"},
                        {"atMs": 1400.0, "worldStatus": "LIVE", "visualStatus": "LIVE"},
                    ],
                    "refreshStartedAtMs": 2000.0,
                    "refreshReadinessSamples": [
                        {"atMs": 2100.0, "worldStatus": "CONNECTING", "visualStatus": "LOADING"},
                        {"atMs": 2600.0, "worldStatus": "LIVE", "visualStatus": "LIVE"},
                    ],
                },
            }
        )
    )


def _valid_summary_inputs(tmp_path) -> dict:
    initial = _world(1)
    robot_1_holding = _world(
        2, moved=True, owner="robot-1", token=1, held_by="robot-1", evidence_suffix="intent-0"
    )
    moving = _world(
        3, moved=True, owner="robot-2", token=2, held_by="robot-2", evidence_suffix="intent-1"
    )
    final = _world(4, moved=True, final=True, owner="environment", token=3)
    run_context = {
        "schemaVersion": "tangying.robocasa-acceptance-run.v1",
        "runId": RUN_ID,
        "episodeNonce": EPISODE_NONCE,
        "taskId": TASK_ID,
        "request": HANDOFF_PROMPT,
        "adapter": "robocasa",
        "sceneId": "robocasa-handoff-v1",
        "publicBaseUrl": "http://127.0.0.1:18080",
        "startedAt": "2026-08-22T00:00:00Z",
        "snapshots": {
            "initial": {
                "revision": 1,
                "projectedAt": initial["projectedAt"],
                "digest": _digest(initial),
            },
            "moving": {
                "revision": 3,
                "projectedAt": moving["projectedAt"],
                "digest": _digest(moving),
            },
            "final": {
                "revision": 4,
                "projectedAt": final["projectedAt"],
                "digest": _digest(final),
            },
        },
    }
    (tmp_path / "run-context.json").write_text(json.dumps(run_context))
    _write_browser_evidence(tmp_path, run_context, final)
    manifest = {
        "sceneId": "robocasa-handoff-v1",
        "modelHash": MODEL_HASH,
        "sceneAsset": "scene.glb",
        "robotModels": {
            "xlerobot": {
                "asset": "xlerobot.glb",
                "binding": "xlerobot.binding.json",
            }
        },
        "contentHashes": {
            "scene.glb": "b" * 64,
            "xlerobot.glb": "c" * 64,
            "xlerobot.binding.json": "d" * 64,
        },
    }
    (tmp_path / "visual-manifest.json").write_text(json.dumps(manifest))
    asset_requests = [
        {
            "url": f"http://127.0.0.1:18080/assets/scenes/robocasa-handoff-v1/{name}",
            "origin": "http://127.0.0.1:18080",
            "bytes": 100,
            "sha256": manifest["contentHashes"][name],
            "expectedSha256": manifest["contentHashes"][name],
            "hashMatches": True,
        }
        for name in ("scene.glb", "xlerobot.glb", "xlerobot.binding.json")
    ]
    (tmp_path / "visual-asset-network.json").write_text(
        json.dumps(
            {
                "schemaVersion": "tangying.asset-network.v1",
                "runId": RUN_ID,
                "episodeNonce": EPISODE_NONCE,
                "taskId": TASK_ID,
                "baseOrigin": "http://127.0.0.1:18080",
                "requests": asset_requests,
                "externalOrigins": [],
                "sameOrigin": True,
                "publicAssetFetchMs": 12.0,
            }
        )
    )
    intents = [
        {
            "index": 0,
            "robotId": "robot-1",
            "claimed": "robot-1",
            "status": "SUCCEEDED",
            "harnessStatus": "SATISFIED",
            "harnessReason": "PHYSICAL_POSTCONDITIONS_SATISFIED",
            "harnessEvidenceIds": [
                robot_1_holding["entities"]["red-block"]["evidence"]["observationId"],
                robot_1_holding["robots"]["robot-1"]["evidence"]["observationId"],
            ],
            "fencingToken": 1,
            "resourceId": "block:red-block",
            "worldRevision": 2,
            "entitySourceId": "robot-1/scene",
            "entitySequenceBasis": 1,
            "robotSourceId": "robot-1/proprioception",
            "robotSequenceBasis": 1,
            "startedAt": "2026-08-22T00:00:01Z",
            "finishedAt": "2026-08-22T00:00:02Z",
        },
        {
            "index": 1,
            "robotId": "robot-2",
            "claimed": "robot-2",
            "status": "SUCCEEDED",
            "harnessStatus": "SATISFIED",
            "harnessReason": "PHYSICAL_POSTCONDITIONS_SATISFIED",
            "harnessEvidenceIds": [
                moving["entities"]["red-block"]["evidence"]["observationId"],
                moving["robots"]["robot-2"]["evidence"]["observationId"],
            ],
            "fencingToken": 2,
            "resourceId": "block:red-block",
            "worldRevision": 3,
            "entitySourceId": "robot-2/scene",
            "entitySequenceBasis": 1,
            "robotSourceId": "robot-2/proprioception",
            "robotSequenceBasis": 1,
            "startedAt": "2026-08-22T00:00:02Z",
            "finishedAt": "2026-08-22T00:00:03Z",
        },
    ]
    events = []
    for intent, event_type, to_owner, to_token, sample in (
        (intents[0], "BLOCK_AVAILABLE", "robot-2", 2, robot_1_holding),
        (intents[1], "BLOCK_DELIVERED", "environment", 3, moving),
    ):
        observations = [
            {
                **sample["entities"]["red-block"]["evidence"],
                "sourceSequence": str(sample["entities"]["red-block"]["evidence"]["sourceSequence"]),
            },
            {
                **sample["robots"][intent["robotId"]]["evidence"],
                "sourceSequence": str(
                    sample["robots"][intent["robotId"]]["evidence"]["sourceSequence"]
                ),
            },
        ]
        events.append(
            {
                "eventType": event_type,
                "aggregateId": TASK_ID,
                "correlationId": TASK_ID,
                "occurredAt": intent["finishedAt"],
                "payload": {
                    "intentIndex": intent["index"],
                    "robotId": intent["robotId"],
                    "harness": {
                        "status": intent["harnessStatus"],
                        "reason": intent["harnessReason"],
                        "evidenceIds": intent["harnessEvidenceIds"],
                        "worldRevision": intent["worldRevision"],
                        "observations": observations,
                    },
                    "resourceTransition": {
                        "resourceId": "block:red-block",
                        "fromOwner": intent["robotId"],
                        "fromFencingToken": intent["fencingToken"],
                        "toOwner": to_owner,
                        "toFencingToken": to_token,
                    },
                },
            }
        )
    (tmp_path / "events.json").write_text(json.dumps(events))
    (tmp_path / "world-trajectory.json").write_text(
        json.dumps(
            {
                "schemaVersion": "tangying.world-trajectory.v1",
                "episodeNonce": EPISODE_NONCE,
                "taskId": TASK_ID,
                "samples": [initial, robot_1_holding, moving, final],
            }
        )
    )
    from scripts.run_robocasa_harness import seal_capture_pack

    trusted_anchor = tmp_path.parent / f"{tmp_path.name}-trusted-anchor.json"
    seal_capture_pack(
        tmp_path,
        run_id=RUN_ID,
        episode_nonce=EPISODE_NONCE,
        task_id=TASK_ID,
        trusted_anchor_path=trusted_anchor,
    )
    return {
        "output": tmp_path,
        "run_context": run_context,
        "task_id": TASK_ID,
        "task": {
            "id": TASK_ID,
            "request": HANDOFF_PROMPT,
            "adapter": "robocasa",
            "state": "SUCCEEDED",
        },
        "initial_world": initial,
        "moving_world": moving,
        "world": final,
        "manifest": manifest,
        "intents": intents,
        "visual": {
            "assetHashesMatch": True,
            "sameOrigin": True,
            "files": {
                "manifest": "visual-manifest.json",
                "network": "visual-network.json",
                "performance": "visual-performance.json",
                "browser": "browser-evidence.json",
            },
        },
        "trusted_anchor_path": trusted_anchor,
    }


def test_acceptance_summary_requires_complete_provenance_bound_evidence(tmp_path):
    from scripts.run_robocasa_harness import build_acceptance_summary

    summary = build_acceptance_summary(**_valid_summary_inputs(tmp_path))

    assert summary["passed"] is True
    assert all(value is True for value in summary["checks"].values())
    assert summary["runId"] == RUN_ID


def test_acceptance_summary_gates_sustained_render_capacity_without_hiding_tail_latency(tmp_path):
    from scripts.run_robocasa_harness import build_acceptance_summary, seal_capture_pack

    values = _valid_summary_inputs(tmp_path)
    performance_path = tmp_path / "visual-performance.json"
    performance = json.loads(performance_path.read_text())
    durations = [6.4] * 144 + [26.0] * 36
    mean_ms = sum(durations) / len(durations)
    performance["raw"]["renderDurationMs"] = durations
    performance["renderCapacityFps"] = 1000 / mean_ms
    performance["renderDurationMeanMs"] = mean_ms
    performance["renderDurationMedianMs"] = 6.4
    performance["renderDurationP90Ms"] = 26.0
    performance["renderDurationP95Ms"] = 26.0
    performance["renderDurationMaxMs"] = 26.0
    performance_path.write_text(json.dumps(performance))
    seal_capture_pack(
        tmp_path,
        run_id=RUN_ID,
        episode_nonce=EPISODE_NONCE,
        task_id=TASK_ID,
        trusted_anchor_path=values["trusted_anchor_path"],
    )

    summary = build_acceptance_summary(**values)

    assert summary["checks"]["browserPerformance"] is True
    assert summary["passed"] is True


def test_observation_epoch_millis_is_not_rounded_back_by_float_conversion():
    from scripts.run_robocasa_harness import _timestamp_epoch_millis

    assert _timestamp_epoch_millis("2026-08-22T00:52:23.366Z") == 1787359943366


def test_capture_receiver_requires_secret_and_seals_browser_bytes(tmp_path):
    from scripts.run_robocasa_harness import AuthenticatedCaptureReceiver

    receiver = AuthenticatedCaptureReceiver(
        tmp_path, run_id=RUN_ID, episode_nonce=EPISODE_NONCE
    )
    receiver.start()
    receiver.bind_task(TASK_ID, tmp_path / "trusted.json")
    payload = {
        "schemaVersion": "tangying.browser-capture-upload.v1",
        "runId": RUN_ID,
        "episodeNonce": EPISODE_NONCE,
        "taskId": TASK_ID,
        "browserEvidence": {
            "schemaVersion": "tangying.browser-acceptance.v1",
            "captures": {"overview": {"capturedAt": "2026-08-22T00:01:23Z"}},
            "screenshots": {"overview": {"capturedAt": "forged"}},
        },
        "performance": {"schemaVersion": "tangying.browser-performance.v1"},
        "screenshots": {"overview": base64.b64encode(GOOD_PNG).decode()},
        "worldSnapshots": {"overview": _world(4, moved=True, final=True, owner="environment", token=3)},
    }
    body = json.dumps(payload).encode()
    try:
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(receiver.url, data=body, method="POST"))
        assert denied.value.code == 401
        request = Request(receiver.url, data=body, method="POST")
        request.add_header("Authorization", f"Bearer {receiver.bearer_secret}")
        request.add_header("Content-Type", "application/json")
        with urlopen(request) as response:
            assert response.status == 201
        assert receiver.wait(2)
        assert (tmp_path / "visual/overview.png").read_bytes() == GOOD_PNG
        assert json.loads((tmp_path / "visual/world-overview.json").read_text())["revision"] == 4
        browser = json.loads((tmp_path / "browser-evidence.json").read_text())
        assert browser["screenshots"]["overview"]["capturedAt"] == "2026-08-22T00:01:23Z"
        assert (tmp_path / "capture-envelope.json").exists()
        assert not (tmp_path / "capture-private-key.pem").exists()
    finally:
        receiver.stop()


def _capture_upload_payload() -> dict:
    return {
        "schemaVersion": "tangying.browser-capture-upload.v1",
        "runId": RUN_ID,
        "episodeNonce": EPISODE_NONCE,
        "taskId": TASK_ID,
        "browserEvidence": {
            "schemaVersion": "tangying.browser-acceptance.v1",
            "captures": {"overview": {"capturedAt": "2026-08-22T00:01:23Z"}},
            "screenshots": {"overview": {"capturedAt": "forged"}},
        },
        "performance": {"schemaVersion": "tangying.browser-performance.v1"},
        "screenshots": {"overview": base64.b64encode(GOOD_PNG).decode()},
        "worldSnapshots": {
            "overview": _world(4, moved=True, final=True, owner="environment", token=3)
        },
    }


def _authenticated_post(receiver, body: bytes) -> int:
    request = Request(receiver.url, data=body, method="POST")
    request.add_header("Authorization", f"Bearer {receiver.bearer_secret}")
    request.add_header("Content-Type", "application/json")
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            return response.status
    except HTTPError as error:
        return error.code


def test_capture_receiver_atomically_accepts_one_of_eight_concurrent_posts(tmp_path):
    from scripts.run_robocasa_harness import AuthenticatedCaptureReceiver

    receiver = AuthenticatedCaptureReceiver(
        tmp_path, run_id=RUN_ID, episode_nonce=EPISODE_NONCE
    )
    receiver.start()
    receiver.bind_task(TASK_ID, tmp_path / "candidate-anchor.json")
    original_accept = receiver._accept

    def slow_accept(payload):
        time.sleep(0.15)
        return original_accept(payload)

    receiver._accept = slow_accept
    body = json.dumps(_capture_upload_payload()).encode()
    barrier = threading.Barrier(8)
    statuses: list[int] = []

    def submit() -> None:
        barrier.wait()
        statuses.append(_authenticated_post(receiver, body))

    workers = [threading.Thread(target=submit) for _ in range(8)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        assert sorted(statuses) == [201] + [409] * 7
    finally:
        receiver.stop()


def test_capture_receiver_releases_uncommitted_malformed_reservation(tmp_path):
    from scripts.run_robocasa_harness import AuthenticatedCaptureReceiver

    receiver = AuthenticatedCaptureReceiver(
        tmp_path, run_id=RUN_ID, episode_nonce=EPISODE_NONCE
    )
    receiver.start()
    receiver.bind_task(TASK_ID, tmp_path / "candidate-anchor.json")
    try:
        assert _authenticated_post(receiver, b"not-json") == 422
        assert _authenticated_post(
            receiver, json.dumps(_capture_upload_payload()).encode()
        ) == 201
    finally:
        receiver.stop()


def test_receiver_keeps_ephemeral_key_until_runner_finalizes_summary(tmp_path):
    from scripts.run_robocasa_harness import AuthenticatedCaptureReceiver

    receiver = AuthenticatedCaptureReceiver(
        tmp_path, run_id=RUN_ID, episode_nonce=EPISODE_NONCE
    )
    receiver.start()
    receiver.bind_task(TASK_ID, tmp_path / "capture-anchor-candidate.json")
    try:
        assert _authenticated_post(
            receiver, json.dumps(_capture_upload_payload()).encode()
        ) == 201
        assert receiver.private_key_active is True

        receiver.finalize(
            {"schemaVersion": "test-summary.v1", "passed": True, "checks": {"test": True}},
            tmp_path / "capture-anchor-candidate.json",
        )

        assert receiver.private_key_active is False
        assert (tmp_path / "acceptance-attestation.json").is_file()
    finally:
        receiver.stop()


def test_repository_capture_uploader_posts_once_without_logging_secret(tmp_path, capsys):
    from scripts.run_robocasa_harness import AuthenticatedCaptureReceiver
    from scripts.upload_robocasa_browser_capture import upload_capture

    receiver = AuthenticatedCaptureReceiver(
        tmp_path, run_id=RUN_ID, episode_nonce=EPISODE_NONCE
    )
    receiver.start()
    receiver.bind_task(TASK_ID, tmp_path / "capture-anchor-candidate.json")
    payload_path = tmp_path / "browser-payload.json"
    payload_path.write_text(json.dumps(_capture_upload_payload()))
    try:
        assert upload_capture(tmp_path / "capture-session.json", payload_path, timeout=5) == 0
        assert receiver.wait(2)
        output = capsys.readouterr()
        assert receiver.bearer_secret not in output.out
        assert receiver.bearer_secret not in output.err
    finally:
        receiver.stop()


@pytest.mark.parametrize("mode", [0o400, 0o700])
def test_repository_capture_uploader_requires_exact_session_mode_0600(tmp_path, mode):
    from scripts.upload_robocasa_browser_capture import UploadError, _load_private_session

    session = tmp_path / "capture-session.json"
    session.write_text(
        json.dumps(
            {
                "schemaVersion": "tangying.capture-session.v1",
                "runId": RUN_ID,
                "episodeNonce": EPISODE_NONCE,
                "taskId": TASK_ID,
                "receiverUrl": "http://127.0.0.1:12345/v1/capture",
                "bearerSecret": "secret",
            }
        )
    )
    session.chmod(mode)

    with pytest.raises(UploadError, match="0600"):
        _load_private_session(session)


def _finalized_pack(tmp_path):
    from scripts.run_robocasa_harness import (
        build_acceptance_summary,
        finalize_acceptance_pack,
    )

    tmp_path.mkdir(parents=True, exist_ok=True)
    values = _valid_summary_inputs(tmp_path)
    for name, value in (
        ("task.json", values["task"]),
        ("intents.json", {"intents": values["intents"]}),
        ("world-initial.json", values["initial_world"]),
        ("world-moving.json", values["moving_world"]),
        ("world-final.json", values["world"]),
    ):
        (tmp_path / name).write_text(json.dumps(value))
    from scripts.run_robocasa_harness import seal_capture_pack

    seal_capture_pack(
        tmp_path,
        run_id=RUN_ID,
        episode_nonce=EPISODE_NONCE,
        task_id=TASK_ID,
        trusted_anchor_path=values["trusted_anchor_path"],
    )
    summary = build_acceptance_summary(**values)
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    candidate_anchor = tmp_path / "capture-anchor-candidate.json"
    finalize_acceptance_pack(
        tmp_path,
        run_id=RUN_ID,
        episode_nonce=EPISODE_NONCE,
        task_id=TASK_ID,
        candidate_anchor_path=candidate_anchor,
    )
    return values, candidate_anchor


def test_final_attestation_rejects_summary_tamper(tmp_path):
    from scripts.run_robocasa_harness import validate_retained_pack

    _values, candidate_anchor = _finalized_pack(tmp_path)
    assert validate_retained_pack(tmp_path, candidate_anchor)
    summary = json.loads((tmp_path / "summary.json").read_text())
    summary["passed"] = False
    (tmp_path / "summary.json").write_text(json.dumps(summary))

    assert not validate_retained_pack(tmp_path, candidate_anchor)


def test_final_attestation_rejects_anchor_public_key_replacement(tmp_path):
    from scripts.run_robocasa_harness import validate_retained_pack

    _values, candidate_anchor = _finalized_pack(tmp_path)
    anchor = json.loads(candidate_anchor.read_text())
    anchor["publicKeyPem"] = anchor["publicKeyPem"].replace("A", "B", 1)
    candidate_anchor.write_text(json.dumps(anchor))

    assert not validate_retained_pack(tmp_path, candidate_anchor)


def test_promotion_uses_the_exact_candidate_anchor_bytes_that_were_validated(
    tmp_path, monkeypatch
):
    import scripts.run_robocasa_harness as harness

    _values, candidate_anchor = _finalized_pack(tmp_path / "candidate")
    validated_bytes = candidate_anchor.read_bytes()
    attacker_anchor = {"schemaVersion": "attacker-anchor.v1", "owned": True}
    trusted_anchor = tmp_path / "trusted-anchor.json"
    original_validate = harness.validate_retained_pack

    def validate_then_swap(output, anchor_path):
        valid = original_validate(output, anchor_path)
        candidate_anchor.write_text(json.dumps(attacker_anchor))
        return valid

    monkeypatch.setattr(harness, "validate_retained_pack", validate_then_swap)

    harness.promote_candidate_anchor(
        tmp_path / "candidate", candidate_anchor, trusted_anchor
    )

    assert trusted_anchor.read_bytes() == validated_bytes


def test_candidate_stack_start_failure_stops_receiver_and_destroys_key(tmp_path, monkeypatch):
    import scripts.run_robocasa_harness as harness

    receivers = []
    real_receiver = harness.AuthenticatedCaptureReceiver

    class RecordingReceiver(real_receiver):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            receivers.append(self)

    def fail_startup(*_args, **_kwargs):
        raise RuntimeError("startup failed")

    monkeypatch.setattr(harness, "AuthenticatedCaptureReceiver", RecordingReceiver)
    monkeypatch.setattr(harness, "start_robocasa_handoff_stack", fail_startup)
    monkeypatch.setattr(harness, "CANDIDATE_ROOT", tmp_path)
    output = tmp_path / "candidate"
    try:
        with pytest.raises(RuntimeError, match="startup failed"):
            harness.run_cli(
                [
                    "--candidate",
                    "--output",
                    str(output),
                    "--browser-evidence-timeout",
                    "1",
                ]
            )

        assert len(receivers) == 1
        assert receivers[0].private_key_active is False
        assert receivers[0]._thread is not None
        assert receivers[0]._thread.is_alive() is False
        assert not (output / "capture-session.json").exists()
    finally:
        for receiver in receivers:
            receiver.stop()


def test_receiver_thread_start_failure_stops_without_hanging_and_destroys_key(tmp_path):
    script = f"""
import threading
from pathlib import Path
from scripts.run_robocasa_harness import AuthenticatedCaptureReceiver

receiver = AuthenticatedCaptureReceiver(
    Path({str(tmp_path)!r}),
    run_id={RUN_ID!r},
    episode_nonce={EPISODE_NONCE!r},
)
original_start = threading.Thread.start

def fail_start(_thread):
    raise RuntimeError("thread start failed")

threading.Thread.start = fail_start
try:
    try:
        receiver.start()
    except RuntimeError as error:
        assert str(error) == "thread start failed"
    else:
        raise AssertionError("receiver start unexpectedly succeeded")
    receiver.stop()
    assert receiver.private_key_active is False
finally:
    threading.Thread.start = original_start
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("receiver.stop() blocked after Thread.start() failed")

    assert completed.returncode == 0, completed.stderr


def test_receiver_cleanup_error_still_closes_server_and_destroys_private_key(tmp_path):
    from scripts.run_robocasa_harness import AuthenticatedCaptureReceiver

    receiver = AuthenticatedCaptureReceiver(
        tmp_path, run_id=RUN_ID, episode_nonce=EPISODE_NONCE
    )
    receiver._key_temp = tempfile.TemporaryDirectory(
        prefix="receiver-cleanup-test-", dir=tmp_path
    )
    receiver._key_root = Path(receiver._key_temp.name)
    receiver._private_key = receiver._key_root / "private.pem"
    receiver._private_key.write_text("ephemeral secret")
    receiver._serve_started.set()

    class BrokenServer:
        closed = False

        def shutdown(self):
            raise RuntimeError("shutdown failed")

        def server_close(self):
            self.closed = True

    class LiveThread:
        joined = False

        def is_alive(self):
            return True

        def join(self, timeout):
            assert timeout == 5
            self.joined = True

    server = BrokenServer()
    thread = LiveThread()
    receiver._server = server
    receiver._thread = thread

    with pytest.raises(RuntimeError, match="shutdown failed"):
        receiver.stop()

    assert server.closed is True
    assert thread.joined is True
    assert receiver.private_key_active is False


def test_nested_control_filename_is_signed_and_tamper_invalidates_pack(tmp_path):
    from scripts.run_robocasa_harness import (
        finalize_acceptance_pack,
        validate_retained_pack,
    )

    _values, candidate_anchor = _finalized_pack(tmp_path)
    nested_control = tmp_path / "visual/capture-session.json"
    nested_control.write_text("retained nested evidence")
    finalize_acceptance_pack(
        tmp_path,
        run_id=RUN_ID,
        episode_nonce=EPISODE_NONCE,
        task_id=TASK_ID,
        candidate_anchor_path=candidate_anchor,
    )
    attestation = json.loads((tmp_path / "acceptance-attestation.json").read_text())

    assert "visual/capture-session.json" in attestation["files"]
    assert validate_retained_pack(tmp_path, candidate_anchor)

    nested_control.write_text("attacker changed nested evidence")
    assert not validate_retained_pack(tmp_path, candidate_anchor)


def _add_unsafe_evidence_entry(output: Path, entry_kind: str) -> Path:
    visual = output / "visual"
    entry = visual / f"unsafe-{entry_kind}"
    if entry_kind == "directory-symlink":
        target = output.parent / "outside-evidence"
        target.mkdir(exist_ok=True)
        entry.symlink_to(target, target_is_directory=True)
    elif entry_kind == "broken-symlink":
        entry.symlink_to(visual / "missing-evidence", target_is_directory=True)
    elif entry_kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("platform cannot create a FIFO safely")
        os.mkfifo(entry)
    else:  # pragma: no cover - test helper contract
        raise AssertionError(f"unknown unsafe evidence kind: {entry_kind}")
    return entry


class _AtomicReadDeadlineExpired(BaseException):
    """Keep a blocking FIFO regression from hanging the pytest process."""


class _atomic_read_deadline:
    def __enter__(self):
        if not hasattr(signal, "setitimer"):
            pytest.skip("platform cannot bound a blocking FIFO open with SIGALRM")
        self._previous = signal.signal(
            signal.SIGALRM,
            lambda _signum, _frame: (_ for _ in ()).throw(
                _AtomicReadDeadlineExpired("evidence read exceeded bounded deadline")
            ),
        )
        signal.setitimer(signal.ITIMER_REAL, 1.0)

    def __exit__(self, *_exc_info):
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self._previous)


def _replace_after_regular_lstat(monkeypatch, victim, replacement: str) -> Path:
    """Replace victim immediately after its audited regular-file metadata is returned."""
    import scripts.run_robocasa_harness as harness

    if replacement == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("platform cannot create a FIFO safely")
    outside = victim.display_path.parent.parent / "outside-atomic-read.txt" if hasattr(
        victim, "display_path"
    ) else victim.parent.parent / "outside-atomic-read.txt"
    outside.write_text("outside evidence must never be read or changed")
    path_type = type(victim)
    original_lstat = path_type.lstat
    replaced = False

    def lstat_then_replace(self):
        nonlocal replaced
        metadata = original_lstat(self)
        if self == victim and not replaced and stat.S_ISREG(metadata.st_mode):
            replaced = True
            replacement_path = self.display_path if hasattr(self, "display_path") else self
            replacement_path.unlink()
            if replacement == "fifo":
                os.mkfifo(replacement_path)
            else:
                replacement_path.symlink_to(outside)
        return metadata

    monkeypatch.setattr(path_type, "lstat", lstat_then_replace)
    if path_type is not harness.FDRootedPath:
        original_fd_lstat = harness.FDRootedPath.lstat

        def fd_lstat_then_replace(self):
            nonlocal replaced
            metadata = original_fd_lstat(self)
            if self.display_path == victim and not replaced and stat.S_ISREG(
                metadata.st_mode
            ):
                replaced = True
                self.display_path.unlink()
                if replacement == "fifo":
                    os.mkfifo(self.display_path)
                else:
                    self.display_path.symlink_to(outside)
            return metadata

        monkeypatch.setattr(harness.FDRootedPath, "lstat", fd_lstat_then_replace)
    return outside


@pytest.mark.parametrize("replacement", ["fifo", "symlink"])
def test_retained_evidence_read_rejects_regular_file_replacement_without_blocking(
    tmp_path, monkeypatch, replacement
):
    import scripts.run_robocasa_harness as harness

    victim = tmp_path / "evidence.json"
    victim.write_text('{"trusted":true}')
    outside = _replace_after_regular_lstat(monkeypatch, victim, replacement)

    with _atomic_read_deadline(), pytest.raises(ValueError, match="evidence file"):
        harness._safe_evidence_files(tmp_path, set())

    assert outside.read_text() == "outside evidence must never be read or changed"


@pytest.mark.parametrize("replacement", ["fifo", "symlink"])
def test_fd_rooted_evidence_read_rejects_regular_file_replacement_without_blocking(
    tmp_path, monkeypatch, replacement
):
    import scripts.run_robocasa_harness as harness

    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    output = harness.FDRootedDirectory(directory_fd, tmp_path)
    victim = output / "evidence.json"
    victim.write_text('{"trusted":true}')
    outside = _replace_after_regular_lstat(monkeypatch, victim, replacement)
    try:
        with _atomic_read_deadline(), pytest.raises(ValueError, match="evidence file"):
            harness._safe_evidence_files(output, set())
    finally:
        os.close(directory_fd)

    assert outside.read_text() == "outside evidence must never be read or changed"


def test_candidate_atomic_read_failure_destroys_key_and_cleans_staging(
    tmp_path, monkeypatch
):
    import scripts.run_robocasa_harness as harness

    root = tmp_path / "robocasa-harness"
    root.mkdir()
    monkeypatch.setattr(harness, "CANDIDATE_ROOT", root)
    prepared = harness._prepare_candidate_output(root / "candidate")
    receiver = harness.AuthenticatedCaptureReceiver(
        prepared.output,
        run_id=RUN_ID,
        episode_nonce=EPISODE_NONCE,
        integrity_check=prepared.assert_integrity,
    )
    receiver.start()
    anchor_path = prepared.output / "capture-anchor-candidate.json"
    receiver.bind_task(TASK_ID, anchor_path)
    victim = prepared.output / "browser-evidence.json"
    victim.write_text('{"trusted":true}')
    harness._write_capture_envelope(
        prepared.output,
        run_id=RUN_ID,
        episode_nonce=EPISODE_NONCE,
        task_id=TASK_ID,
        private_key=receiver._private_key,
        public_pem=receiver._public_pem,
        public_fingerprint=receiver._public_fingerprint,
        key_directory=receiver._key_root,
    )
    receiver._received.set()
    outside = _replace_after_regular_lstat(monkeypatch, victim, "fifo")
    try:
        with _atomic_read_deadline(), pytest.raises(ValueError, match="evidence file"):
            receiver.finalize(
                {
                    "schemaVersion": "test-summary.v1",
                    "passed": True,
                    "checks": {"test": True},
                },
                anchor_path,
            )
        assert receiver.private_key_active is False
        assert not anchor_path.exists()
        assert not (prepared.output / "acceptance-attestation.json").exists()
    finally:
        receiver.stop()
        prepared.cleanup()

    assert outside.read_text() == "outside evidence must never be read or changed"
    outside.unlink()
    assert list(root.iterdir()) == []


def _replace_ancestor_after_regular_audit(
    monkeypatch, harness, victim: Path
) -> tuple[Path, Path]:
    """Swap victim's parent after either Path or fd-rooted audit returns."""
    parent = victim.parent
    outside = parent.with_name(parent.name + "-outside")
    outside.mkdir()
    detached = outside / "moved-audited-parent"
    outside_marker = outside / "outside-marker.txt"
    outside_marker.write_text("outside evidence must never be read or changed")
    original_path_lstat = type(victim).lstat
    original_fd_lstat = harness.FDRootedPath.lstat
    original_os_stat = harness.os.stat
    parent_identity = tuple(
        getattr(original_os_stat(parent), field) for field in ("st_dev", "st_ino")
    )
    replaced = False

    def replace_once(metadata):
        nonlocal replaced
        if not replaced and stat.S_ISREG(metadata.st_mode):
            replaced = True
            parent.rename(detached)
            parent.symlink_to(detached, target_is_directory=True)
        return metadata

    def path_lstat_then_replace(self):
        metadata = original_path_lstat(self)
        return replace_once(metadata) if self == victim else metadata

    def fd_lstat_then_replace(self):
        metadata = original_fd_lstat(self)
        return replace_once(metadata) if self.display_path == victim else metadata

    def stat_then_replace(path, *args, **kwargs):
        metadata = original_os_stat(path, *args, **kwargs)
        directory_fd = kwargs.get("dir_fd")
        if (
            path == victim.name
            and directory_fd is not None
            and tuple(
                getattr(os.fstat(directory_fd), field) for field in ("st_dev", "st_ino")
            )
            == parent_identity
        ):
            return replace_once(metadata)
        return metadata

    monkeypatch.setattr(type(victim), "lstat", path_lstat_then_replace)
    monkeypatch.setattr(harness.FDRootedPath, "lstat", fd_lstat_then_replace)
    monkeypatch.setattr(harness.os, "stat", stat_then_replace)
    return outside_marker, detached


def test_retained_enumeration_rejects_audited_file_ancestor_replacement(
    tmp_path, monkeypatch
):
    import scripts.run_robocasa_harness as harness

    output = tmp_path / "retained"
    visual = output / "visual"
    visual.mkdir(parents=True)
    victim = visual / "evidence.json"
    victim.write_text('{"trusted":true}')
    outside_marker, _detached = _replace_ancestor_after_regular_audit(
        monkeypatch, harness, victim
    )

    with pytest.raises(ValueError, match="evidence"):
        harness._safe_evidence_files(output, set())

    assert outside_marker.read_text() == "outside evidence must never be read or changed"


@pytest.mark.parametrize("operation", ["read", "hash"])
def test_direct_evidence_read_rejects_audited_file_ancestor_replacement(
    tmp_path, monkeypatch, operation
):
    import scripts.run_robocasa_harness as harness

    parent = tmp_path / "visual"
    parent.mkdir()
    victim = parent / "evidence.json"
    victim.write_text('{"trusted":true}')
    outside_marker, _detached = _replace_ancestor_after_regular_audit(
        monkeypatch, harness, victim
    )

    action = (
        lambda: harness._read_audited_regular_file(victim)
    ) if operation == "read" else (
        lambda: harness._sha256_audited_regular_file(victim)
    )
    with pytest.raises(ValueError, match="evidence"):
        action()

    assert outside_marker.read_text() == "outside evidence must never be read or changed"


def test_promotion_rejects_candidate_anchor_ancestor_replacement(tmp_path, monkeypatch):
    import scripts.run_robocasa_harness as harness

    output = tmp_path / "candidate"
    output.mkdir()
    candidate_anchor = output / "capture-anchor-candidate.json"
    candidate_anchor.write_text('{"candidate":true}')
    outside_marker, _detached = _replace_ancestor_after_regular_audit(
        monkeypatch, harness, candidate_anchor
    )
    trusted_anchor = tmp_path / "trusted-anchor.json"

    with pytest.raises(AssertionError, match="candidate anchor"):
        harness.promote_candidate_anchor(output, candidate_anchor, trusted_anchor)

    assert not trusted_anchor.exists()
    assert outside_marker.read_text() == "outside evidence must never be read or changed"


@pytest.mark.parametrize("operation", ["read", "hash", "fd-rooted-read"])
def test_fdopen_failure_closes_every_owned_evidence_descriptor(
    tmp_path, monkeypatch, operation
):
    import scripts.run_robocasa_harness as harness

    victim = tmp_path / "evidence.json"
    victim.write_text('{"trusted":true}')
    directory_fd = None
    target = victim
    if operation == "fd-rooted-read":
        directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        target = harness.FDRootedDirectory(directory_fd, tmp_path) / victim.name
    def open_descriptors() -> set[int]:
        descriptors = set()
        for descriptor in range(256):
            try:
                os.fstat(descriptor)
            except OSError:
                continue
            descriptors.add(descriptor)
        return descriptors

    before = open_descriptors()

    def fail_fdopen(*_args, **_kwargs):
        raise RuntimeError("injected fdopen failure")

    monkeypatch.setattr(harness.os, "fdopen", fail_fdopen)
    try:
        with pytest.raises(RuntimeError, match="injected fdopen failure"):
            if operation == "read":
                harness._read_audited_regular_file(target)
            elif operation == "hash":
                harness._sha256_audited_regular_file(target)
            else:
                target.read_bytes()
        after = open_descriptors()
        leaked = after - before
        assert leaked == set()
    finally:
        for descriptor in locals().get("leaked", set()):
            os.close(descriptor)
        if directory_fd is not None:
            os.close(directory_fd)


@pytest.mark.parametrize("entry_kind", ["directory-symlink", "broken-symlink", "fifo"])
def test_retained_validation_rejects_non_regular_evidence_entries(tmp_path, entry_kind):
    from scripts.run_robocasa_harness import validate_retained_pack

    _values, candidate_anchor = _finalized_pack(tmp_path)
    _add_unsafe_evidence_entry(tmp_path, entry_kind)

    assert not validate_retained_pack(tmp_path, candidate_anchor)


@pytest.mark.parametrize("entry_kind", ["directory-symlink", "broken-symlink", "fifo"])
def test_candidate_finalization_rejects_non_regular_evidence_entries(
    tmp_path, monkeypatch, entry_kind
):
    import scripts.run_robocasa_harness as harness

    candidate_root = tmp_path / "robocasa-harness"
    candidate_root.mkdir()
    monkeypatch.setattr(harness, "CANDIDATE_ROOT", candidate_root)
    prepared = harness._prepare_candidate_output(candidate_root / "candidate")
    try:
        display_path = prepared.output.display_path
        values = _valid_summary_inputs(display_path)
        summary = harness.build_acceptance_summary(**values)
        (prepared.output / "summary.json").write_text(json.dumps(summary))
        _add_unsafe_evidence_entry(display_path, entry_kind)
        candidate_anchor = prepared.output / "capture-anchor-candidate.json"

        with pytest.raises(ValueError, match="unsafe evidence tree"):
            harness.finalize_acceptance_pack(
                prepared.output,
                run_id=RUN_ID,
                episode_nonce=EPISODE_NONCE,
                task_id=TASK_ID,
                candidate_anchor_path=candidate_anchor,
            )

        assert not (prepared.output / "acceptance-attestation.json").exists()
        assert not candidate_anchor.exists()
    finally:
        prepared.cleanup()


def test_candidate_preparation_removes_unknown_top_level_and_nested_residue(
    tmp_path, monkeypatch
):
    import scripts.run_robocasa_harness as harness

    monkeypatch.setattr(harness, "CANDIDATE_ROOT", tmp_path)
    output = tmp_path / "candidate"
    (output / "nested").mkdir(parents=True)
    (output / "stale-secret.txt").write_text("old bearer")
    (output / "nested/stale.json").write_text("old evidence")

    prepared = harness._prepare_candidate_output(output)

    assert prepared.requested == output.resolve()
    assert list(prepared.requested.iterdir()) == []
    assert list(prepared.output.iterdir()) == []
    prepared.cleanup()


@pytest.mark.parametrize("target_name", ["outside", "round3"])
def test_candidate_replacement_never_redirects_receiver_or_runner_writes(
    tmp_path, monkeypatch, target_name
):
    """Catches reusing the user-visible candidate path after preparation."""
    import scripts.run_robocasa_harness as harness

    root = tmp_path / "robocasa-harness"
    root.mkdir()
    target = root / target_name if target_name == "round3" else tmp_path / target_name
    target.mkdir()
    marker = target / "marker.txt"
    marker.write_text("must survive")
    candidate = root / "candidate"
    monkeypatch.setattr(harness, "CANDIDATE_ROOT", root)
    prepared = harness._prepare_candidate_output(candidate)
    candidate.rmdir()
    candidate.symlink_to(target, target_is_directory=True)

    receiver = harness.AuthenticatedCaptureReceiver(
        prepared.output, run_id=RUN_ID, episode_nonce=EPISODE_NONCE
    )
    try:
        receiver.start()
        harness.write_json(prepared.output / "runner-evidence.json", {"safe": True})

        assert marker.read_text() == "must survive"
        assert not (target / "capture-session.json").exists()
        assert not (target / "runner-evidence.json").exists()
        with pytest.raises(SystemExit, match="changed|replaced"):
            prepared.publish()
    finally:
        receiver.stop()
        prepared.cleanup()


@pytest.mark.parametrize("target_name", ["outside", "round3"])
def test_staging_entry_swap_after_precheck_never_writes_or_deletes_target(
    tmp_path, monkeypatch, target_name
):
    """Catches check/use/check Path I/O after a held staging descriptor was verified."""
    import scripts.run_robocasa_harness as harness

    root = tmp_path / "robocasa-harness"
    root.mkdir()
    target = root / target_name if target_name == "round3" else tmp_path / target_name
    target.mkdir()
    target_session = target / "capture-session.json"
    target_session.write_text("must survive unchanged")
    monkeypatch.setattr(harness, "CANDIDATE_ROOT", root)
    prepared = harness._prepare_candidate_output(root / "candidate")
    staging_path = getattr(prepared.output, "display_path", prepared.output)
    detached = root / "detached-staging"
    original_check = prepared.assert_integrity
    swapped = False

    def check_then_swap():
        nonlocal swapped
        original_check()
        if not swapped:
            staging_path.rename(detached)
            staging_path.symlink_to(target, target_is_directory=True)
            swapped = True

    receiver = harness.AuthenticatedCaptureReceiver(
        prepared.output,
        run_id=RUN_ID,
        episode_nonce=EPISODE_NONCE,
        integrity_check=check_then_swap,
    )
    try:
        with pytest.raises(SystemExit, match="staging|changed"):
            receiver.start()
    finally:
        receiver.stop()
        prepared.cleanup()

    assert target_session.read_text() == "must survive unchanged"
    assert not detached.exists()
    assert receiver.private_key_active is False


def test_staging_swap_keeps_capture_summary_and_attestation_on_held_fd(
    tmp_path, monkeypatch
):
    """Catches any receiver/finalizer operation that falls back to the staging pathname."""
    import scripts.run_robocasa_harness as harness

    root = tmp_path / "robocasa-harness"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    marker = outside / "capture-session.json"
    marker.write_text("outside must remain unchanged")
    monkeypatch.setattr(harness, "CANDIDATE_ROOT", root)
    prepared = harness._prepare_candidate_output(root / "candidate")
    receiver = harness.AuthenticatedCaptureReceiver(
        prepared.output,
        run_id=RUN_ID,
        episode_nonce=EPISODE_NONCE,
        integrity_check=prepared.assert_integrity,
    )
    receiver.start()
    anchor_path = prepared.output / "capture-anchor-candidate.json"
    receiver.bind_task(TASK_ID, anchor_path)
    staging_path = prepared.output.display_path
    detached = root / "detached-staging"
    original_check = prepared.assert_integrity
    swapped = False

    def check_then_swap():
        nonlocal swapped
        original_check()
        if not swapped:
            staging_path.rename(detached)
            staging_path.symlink_to(outside, target_is_directory=True)
            swapped = True

    receiver._integrity_check = check_then_swap
    try:
        with pytest.raises(SystemExit, match="staging|changed"):
            receiver._accept(_capture_upload_payload())

        receiver._integrity_check = lambda: None
        receiver._received.set()
        receiver.finalize(
            {"schemaVersion": "test-summary.v1", "passed": True, "checks": {"test": True}},
            anchor_path,
        )
        assert (prepared.output / "visual/overview.png").is_file()
        assert (prepared.output / "browser-evidence.json").is_file()
        assert (prepared.output / "summary.json").is_file()
        assert (prepared.output / "capture-envelope.json").is_file()
        assert (prepared.output / "acceptance-attestation.json").is_file()
        assert (prepared.output / "capture-anchor-candidate.json").is_file()
    finally:
        receiver.stop()
        prepared.cleanup()

    assert marker.read_text() == "outside must remain unchanged"
    assert not detached.exists()
    assert receiver.private_key_active is False


def test_publish_returns_fd_rooted_content_if_candidate_is_replaced_after_check(
    tmp_path, monkeypatch
):
    """Catches returning the mutable candidate pathname after final identity validation."""
    import scripts.run_robocasa_harness as harness

    root = tmp_path / "robocasa-harness"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "marker.txt").write_text("outside")
    candidate = root / "candidate"
    monkeypatch.setattr(harness, "CANDIDATE_ROOT", root)
    prepared = harness._prepare_candidate_output(candidate)
    harness.write_json(prepared.output / "published.json", {"trusted": True})
    detached = root / "detached-published"
    original_identity = prepared._entry_identity
    replaced = False

    def identity_then_replace(name, parent_fd):
        nonlocal replaced
        identity = original_identity(name, parent_fd)
        if name == candidate.name and identity == prepared._staging_identity and not replaced:
            candidate.rename(detached)
            candidate.symlink_to(outside, target_is_directory=True)
            replaced = True
        return identity

    monkeypatch.setattr(prepared, "_entry_identity", identity_then_replace)
    try:
        published = prepared.publish()
        assert json.loads((published / "published.json").read_text()) == {
            "trusted": True
        }
        assert (outside / "marker.txt").read_text() == "outside"
        assert not (outside / "published.json").exists()
    finally:
        prepared.cleanup()


def test_candidate_root_may_use_trusted_parent_symlink_but_children_may_not(
    tmp_path, monkeypatch
):
    """Catches applying no-follow above the trusted canonical artifacts root."""
    import scripts.run_robocasa_harness as harness

    real_root = tmp_path / "private" / "robocasa-harness"
    real_root.mkdir(parents=True)
    linked_root = tmp_path / "var" / "robocasa-harness"
    linked_root.parent.symlink_to(real_root.parent, target_is_directory=True)
    monkeypatch.setattr(harness, "CANDIDATE_ROOT", linked_root)

    prepared = harness._prepare_candidate_output(linked_root / "candidate")
    try:
        assert prepared.requested == real_root / "candidate"
        assert prepared.output.is_dir()
        harness.write_json(prepared.output / "published.json", {"safe": True})
        published = prepared.publish()
        assert published.display_path == real_root / "candidate"
        assert json.loads((published / "published.json").read_text()) == {
            "safe": True
        }
    finally:
        prepared.cleanup()

    valuable = real_root / "valuable"
    valuable.mkdir()
    linked_child = real_root / "linked-child"
    linked_child.symlink_to(valuable, target_is_directory=True)
    with pytest.raises(SystemExit, match="symlink"):
        harness._prepare_candidate_output(linked_root / "linked-child" / "candidate")
    assert not (valuable / "candidate").exists()


def test_candidate_preparation_rejects_symlink_and_preserves_its_target(
    tmp_path, monkeypatch
):
    import scripts.run_robocasa_harness as harness

    root = tmp_path / "robocasa-harness"
    valuable = root / "valuable-pack"
    valuable.mkdir(parents=True)
    marker = valuable / "marker.txt"
    marker.write_text("must survive")
    candidate = root / "candidate"
    candidate.symlink_to(valuable, target_is_directory=True)
    monkeypatch.setattr(harness, "CANDIDATE_ROOT", root)

    with pytest.raises(SystemExit, match="symlink"):
        harness._prepare_candidate_output(candidate)

    assert candidate.is_symlink()
    assert marker.read_text() == "must survive"


def test_candidate_preparation_rejects_symlinked_parent_component(
    tmp_path, monkeypatch
):
    import scripts.run_robocasa_harness as harness

    root = tmp_path / "robocasa-harness"
    real_parent = root / "real-parent"
    real_parent.mkdir(parents=True)
    marker = real_parent / "marker.txt"
    marker.write_text("must survive")
    linked_parent = root / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    monkeypatch.setattr(harness, "CANDIDATE_ROOT", root)

    with pytest.raises(SystemExit, match="symlink"):
        harness._prepare_candidate_output(linked_parent / "candidate")

    assert linked_parent.is_symlink()
    assert marker.read_text() == "must survive"
    assert not (real_parent / "candidate").exists()


def test_candidate_preparation_fails_closed_when_parent_is_swapped_to_symlink(
    tmp_path, monkeypatch
):
    import scripts.run_robocasa_harness as harness

    root = tmp_path / "robocasa-harness"
    parent = root / "parent"
    parent.mkdir(parents=True)
    detached_parent = root / "detached-parent"
    valuable = root / "valuable-pack"
    valuable.mkdir()
    marker = valuable / "marker.txt"
    marker.write_text("must survive")
    original_open = harness._open_directory_tree_no_symlinks
    swapped = False

    def open_then_swap(path, **kwargs):
        nonlocal swapped
        directory_fd = original_open(path, **kwargs)
        if not swapped:
            parent.rename(detached_parent)
            parent.symlink_to(valuable, target_is_directory=True)
            swapped = True
        return directory_fd

    monkeypatch.setattr(harness, "CANDIDATE_ROOT", root)
    monkeypatch.setattr(harness, "_open_directory_tree_no_symlinks", open_then_swap)

    with pytest.raises(SystemExit, match="symlink|changed"):
        harness._prepare_candidate_output(parent / "candidate")

    assert marker.read_text() == "must survive"
    assert not (valuable / "candidate").exists()


def test_candidate_preparation_rejects_outside_root_and_preserves_round3(
    tmp_path, monkeypatch
):
    import scripts.run_robocasa_harness as harness

    root = tmp_path / "robocasa-harness"
    round3 = root / "round3"
    round3.mkdir(parents=True)
    marker = round3 / "summary.json"
    marker.write_text("retained")
    monkeypatch.setattr(harness, "CANDIDATE_ROOT", root)

    with pytest.raises(SystemExit, match="inside"):
        harness._prepare_candidate_output(tmp_path / "outside")
    with pytest.raises(SystemExit, match="round3"):
        harness._prepare_candidate_output(round3)

    assert marker.read_text() == "retained"


def test_candidate_promote_and_retained_revalidate_cli_never_start_a_stack(
    tmp_path, monkeypatch
):
    import scripts.run_robocasa_harness as harness

    _values, candidate_anchor = _finalized_pack(tmp_path / "candidate")
    assert candidate_anchor.exists()
    tracked_anchor = tmp_path / "trusted-anchor.json"

    def forbidden_stack(*_args, **_kwargs):
        raise AssertionError("audit and revalidation must not start a stack")

    monkeypatch.setattr(harness, "start_robocasa_handoff_stack", forbidden_stack)
    assert harness.run_cli(
        [
            "--promote-anchor",
            "--output",
            str(tmp_path / "candidate"),
            "--anchor",
            str(tracked_anchor),
        ]
    ) == 0
    assert harness.run_cli(
        [
            "--revalidate",
            "--output",
            str(tmp_path / "candidate"),
            "--anchor",
            str(tracked_anchor),
        ]
    ) == 0


def test_candidate_cli_rejects_zero_browser_wait_before_starting_stack(tmp_path, monkeypatch):
    import scripts.run_robocasa_harness as harness

    def forbidden_stack(*_args, **_kwargs):
        raise AssertionError("zero-timeout candidate must fail before stack startup")

    monkeypatch.setattr(harness, "start_robocasa_handoff_stack", forbidden_stack)
    with pytest.raises(SystemExit):
        harness.run_cli(
            [
                "--candidate",
                "--output",
                str(tmp_path),
                "--browser-evidence-timeout",
                "0",
            ]
        )


def test_make_acceptance_workflows_separate_revalidate_candidate_and_promotion():
    commands = {
        target: subprocess.run(
            ["make", "-n", target], check=True, capture_output=True, text=True
        ).stdout
        for target in (
            "robocasa-acceptance",
            "robocasa-acceptance-candidate",
            "robocasa-acceptance-promote",
        )
    }

    assert "--revalidate" in commands["robocasa-acceptance"]
    assert "artifacts/robocasa-harness/round3" in commands["robocasa-acceptance"]
    assert "--candidate" in commands["robocasa-acceptance-candidate"]
    assert "--browser-evidence-timeout" in commands["robocasa-acceptance-candidate"]
    assert "--promote-anchor" in commands["robocasa-acceptance-promote"]


def test_harness_accepts_registered_alternate_scene_observation(tmp_path):
    from scripts.run_robocasa_harness import build_acceptance_summary

    values = _valid_summary_inputs(tmp_path)
    events = json.loads((tmp_path / "events.json").read_text())
    trajectory = json.loads((tmp_path / "world-trajectory.json").read_text())
    old = events[0]["payload"]["harness"]["observations"][0]
    new_id = old["observationId"].replace("robot-1/scene/", "robot-2/scene/")
    old["sourceId"] = "robot-2/scene"
    old["observationId"] = new_id
    events[0]["payload"]["harness"]["evidenceIds"][0] = new_id
    values["intents"][0]["harnessEvidenceIds"][0] = new_id
    trajectory["samples"][1]["entities"]["red-block"]["evidence"]["sourceId"] = "robot-2/scene"
    trajectory["samples"][1]["entities"]["red-block"]["evidence"]["observationId"] = new_id
    (tmp_path / "events.json").write_text(json.dumps(events))
    (tmp_path / "world-trajectory.json").write_text(json.dumps(trajectory))

    summary = build_acceptance_summary(**values)

    assert summary["checks"]["harnessEvidence"] is True


@pytest.mark.parametrize(
    ("case", "failed_check"),
    [
        ("boolean_joint", "canonicalJoints"),
        ("malformed_model_hash", "modelIdentity"),
        ("wrong_task_id", "taskIdentity"),
        ("wrong_request", "taskIdentity"),
        ("wrong_adapter", "taskIdentity"),
        ("wrong_scene", "sceneIdentity"),
        ("wrong_intent_index", "intents"),
        ("wrong_intent_robot", "intents"),
        ("wrong_intent_status", "intents"),
        ("wrong_harness_reason", "intents"),
        ("empty_harness_evidence", "intents"),
        ("robot_not_moved", "jointMovement"),
        ("final_held", "finalHeldClear"),
        ("stale_source", "sourceFreshness"),
        ("non_monotonic_fencing", "custody"),
        ("missing_screenshot", "screenshots"),
        ("one_pixel_screenshot", "screenshots"),
        ("black_screenshot", "screenshots"),
        ("stripe_screenshot", "screenshots"),
        ("wrong_viewport", "screenshots"),
        ("wrong_capture_state", "screenshots"),
        ("fallback_missing_live_badge", "screenshots"),
        ("fallback_wrong_canvas", "screenshots"),
        ("jpeg_named_png", "screenshots"),
        ("corrupt_png", "screenshots"),
        ("old_browser_run", "provenance"),
        ("wrong_episode_nonce", "provenance"),
        ("wrong_snapshot_digest", "provenance"),
        ("external_browser_origin", "browserNetwork"),
        ("external_browser_response_origin", "browserNetwork"),
        ("slow_first_interaction", "browserPerformance"),
        ("low_steady_fps", "browserPerformance"),
        ("raw_low_steady_fps", "browserPerformance"),
        ("synthetic_fps", "browserPerformance"),
        ("slow_render_capacity", "browserPerformance"),
        ("truncated_render_timestamps", "browserPerformance"),
        ("constant_render_timestamps", "browserPerformance"),
        ("slow_refresh", "browserPerformance"),
        ("old_asset_run", "assetContentHashes"),
        ("external_asset_origin", "assetSameOrigin"),
        ("missing_manifest_artifact", "assetContentHashes"),
        ("equal_snapshot_revision", "snapshotOrder"),
        ("backward_projected_at", "snapshotOrder"),
        ("missing_robot_1_custody", "custodyTrajectory"),
        ("fabricated_evidence", "harnessEvidence"),
        ("wrong_event_transition", "harnessEvidence"),
        ("wrong_event_correlation", "harnessEvidence"),
        ("wrong_observation_id", "harnessEvidence"),
        ("wrong_observation_source", "harnessEvidence"),
        ("wrong_observation_sequence", "harnessEvidence"),
        ("wrong_observation_time", "harnessEvidence"),
        ("wrong_observation_frame", "harnessEvidence"),
        ("wrong_observation_transform", "harnessEvidence"),
        ("capture_nonce_substitution", "captureAuthentication"),
        ("capture_public_key_replacement", "captureAuthentication"),
        ("capture_signature_tamper", "captureAuthentication"),
        ("truncated_runner_network", "browserNetwork"),
    ],
)
def test_acceptance_summary_rejects_each_adversarial_bypass(tmp_path, case, failed_check):
    from scripts.run_robocasa_harness import build_acceptance_summary

    values = _valid_summary_inputs(tmp_path)
    if case == "boolean_joint":
        values["world"]["robots"]["robot-1"]["state"]["joint.arm.0"] = True
    elif case == "malformed_model_hash":
        values["world"]["entities"]["robot-1"]["attributes"]["model_hash"] = "A" * 64
        values["world"]["entities"]["robot-2"]["attributes"]["model_hash"] = "A" * 64
        values["manifest"]["modelHash"] = "A" * 64
    elif case == "wrong_task_id":
        values["task"]["id"] = "task-other"
    elif case == "wrong_request":
        values["task"]["request"] = "move something"
    elif case == "wrong_adapter":
        values["task"]["adapter"] = "mujoco"
    elif case == "wrong_scene":
        values["world"]["worldId"] = "other-world"
    elif case == "wrong_intent_index":
        values["intents"][1]["index"] = 0
    elif case == "wrong_intent_robot":
        values["intents"][1]["robotId"] = "robot-1"
    elif case == "wrong_intent_status":
        values["intents"][0]["status"] = "FAILED"
    elif case == "wrong_harness_reason":
        values["intents"][0]["harnessReason"] = ""
    elif case == "empty_harness_evidence":
        values["intents"][0]["harnessEvidenceIds"] = []
    elif case == "robot_not_moved":
        values["moving_world"]["robots"]["robot-2"]["state"] = copy.deepcopy(
            values["initial_world"]["robots"]["robot-2"]["state"]
        )
    elif case == "final_held":
        values["world"]["robots"]["robot-2"]["held"] = "red-block"
    elif case == "stale_source":
        values["world"]["sources"]["robot-2/scene"]["freshness"] = "STALE"
    elif case == "non_monotonic_fencing":
        values["world"]["resources"]["block:red-block"]["fencingToken"] = 2
    elif case == "missing_screenshot":
        (tmp_path / "visual/overview.png").unlink()
    elif case in {"one_pixel_screenshot", "black_screenshot"}:
        payload = PNG_1X1 if case == "one_pixel_screenshot" else _substantial_png(black=True)
        path = tmp_path / "visual/overview.png"
        path.write_bytes(payload)
        browser = json.loads((tmp_path / "browser-evidence.json").read_text())
        browser["screenshots"]["overview"]["sha256"] = hashlib.sha256(payload).hexdigest()
        browser["screenshots"]["overview"]["bytes"] = len(payload)
        (tmp_path / "browser-evidence.json").write_text(json.dumps(browser))
    elif case == "stripe_screenshot":
        payload = _substantial_png()
        image = Image.new("RGB", VIEWPORT, "#263238")
        draw = ImageDraw.Draw(image)
        for x in range(0, VIEWPORT[0], 32):
            draw.rectangle((x, 0, x + 15, VIEWPORT[1]), fill="#607d8b")
        stream = io.BytesIO()
        image.save(stream, "PNG")
        payload = stream.getvalue()
        path = tmp_path / "visual/overview.png"
        path.write_bytes(payload)
        browser = json.loads((tmp_path / "browser-evidence.json").read_text())
        browser["screenshots"]["overview"]["sha256"] = hashlib.sha256(payload).hexdigest()
        browser["screenshots"]["overview"]["bytes"] = len(payload)
        (tmp_path / "browser-evidence.json").write_text(json.dumps(browser))
    elif case == "wrong_viewport":
        browser = json.loads((tmp_path / "browser-evidence.json").read_text())
        browser["captures"]["overview"]["viewport"]["width"] = 1403
        (tmp_path / "browser-evidence.json").write_text(json.dumps(browser))
    elif case == "wrong_capture_state":
        browser = json.loads((tmp_path / "browser-evidence.json").read_text())
        browser["captures"]["robot-1"]["captureName"] = "overview"
        (tmp_path / "browser-evidence.json").write_text(json.dumps(browser))
    elif case == "fallback_missing_live_badge":
        browser = json.loads((tmp_path / "browser-evidence.json").read_text())
        browser["captures"]["fallback"]["document"]["worldBadge"]["text"] = "WORLD STALE"
        (tmp_path / "browser-evidence.json").write_text(json.dumps(browser))
    elif case == "fallback_wrong_canvas":
        browser = json.loads((tmp_path / "browser-evidence.json").read_text())
        browser["captures"]["fallback"]["document"]["canvas"]["id"] = "fleet-godview-webgl"
        (tmp_path / "browser-evidence.json").write_text(json.dumps(browser))
    elif case == "jpeg_named_png":
        (tmp_path / "visual/overview.png").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    elif case == "corrupt_png":
        (tmp_path / "visual/overview.png").write_bytes(PNG_1X1[:-8])
    elif case == "old_browser_run":
        browser = json.loads((tmp_path / "browser-evidence.json").read_text())
        browser["runId"] = "f" * 32
        (tmp_path / "browser-evidence.json").write_text(json.dumps(browser))
    elif case == "wrong_episode_nonce":
        browser = json.loads((tmp_path / "browser-evidence.json").read_text())
        browser["episodeNonce"] = "0" * 64
        (tmp_path / "browser-evidence.json").write_text(json.dumps(browser))
    elif case == "wrong_snapshot_digest":
        browser = json.loads((tmp_path / "browser-evidence.json").read_text())
        browser["snapshots"]["overview"]["digest"] = "0" * 64
        (tmp_path / "browser-evidence.json").write_text(json.dumps(browser))
    elif case == "external_browser_origin":
        network = json.loads((tmp_path / "visual-network.json").read_text())
        network["observedURLs"].append("https://cdn.example/scene.glb")
        network["externalOrigins"] = ["https://cdn.example"]
        network["sameOrigin"] = False
        (tmp_path / "visual-network.json").write_text(json.dumps(network))
    elif case == "external_browser_response_origin":
        network = json.loads((tmp_path / "visual-network.json").read_text())
        network["requests"][0]["responseUrl"] = "https://cdn.example/app.js"
        (tmp_path / "visual-network.json").write_text(json.dumps(network))
    elif case == "slow_first_interaction":
        performance = json.loads((tmp_path / "visual-performance.json").read_text())
        performance["firstInteractionMs"] = 5001
        (tmp_path / "visual-performance.json").write_text(json.dumps(performance))
    elif case == "low_steady_fps":
        performance = json.loads((tmp_path / "visual-performance.json").read_text())
        performance["steadyFps"] = 49.9
        (tmp_path / "visual-performance.json").write_text(json.dumps(performance))
    elif case == "raw_low_steady_fps":
        performance = json.loads((tmp_path / "visual-performance.json").read_text())
        performance["steadyFps"] = 120.0
        performance["raw"]["frameTimesMs"] = [1000.0 + index * 25.0 for index in range(121)]
        (tmp_path / "visual-performance.json").write_text(json.dumps(performance))
    elif case == "synthetic_fps":
        performance = json.loads((tmp_path / "visual-performance.json").read_text())
        performance["raw"]["frameTimesMs"] = [1000.0 + index * 16.6667 for index in range(121)]
        performance["steadyFps"] = 60.0
        (tmp_path / "visual-performance.json").write_text(json.dumps(performance))
    elif case == "slow_render_capacity":
        performance = json.loads((tmp_path / "visual-performance.json").read_text())
        performance["raw"]["renderDurationMs"] = [24.0 + (index % 7) * 0.1 for index in range(180)]
        performance["renderCapacityFps"] = 40.7
        (tmp_path / "visual-performance.json").write_text(json.dumps(performance))
    elif case == "truncated_render_timestamps":
        performance = json.loads((tmp_path / "visual-performance.json").read_text())
        performance["raw"]["renderDurationTimestampsMs"] = performance["raw"][
            "renderDurationTimestampsMs"
        ][:-1]
        (tmp_path / "visual-performance.json").write_text(json.dumps(performance))
    elif case == "constant_render_timestamps":
        performance = json.loads((tmp_path / "visual-performance.json").read_text())
        performance["raw"]["renderDurationTimestampsMs"] = [3000.0] * len(
            performance["raw"]["renderDurationMs"]
        )
        (tmp_path / "visual-performance.json").write_text(json.dumps(performance))
    elif case == "slow_refresh":
        performance = json.loads((tmp_path / "visual-performance.json").read_text())
        performance["refreshRecoveryMs"] = 5001
        (tmp_path / "visual-performance.json").write_text(json.dumps(performance))
    elif case == "old_asset_run":
        network = json.loads((tmp_path / "visual-asset-network.json").read_text())
        network["runId"] = "f" * 32
        (tmp_path / "visual-asset-network.json").write_text(json.dumps(network))
    elif case == "external_asset_origin":
        network = json.loads((tmp_path / "visual-asset-network.json").read_text())
        network["requests"][0]["url"] = "https://cdn.example/scene.glb"
        network["requests"][0]["origin"] = "https://cdn.example"
        network["externalOrigins"] = ["https://cdn.example"]
        network["sameOrigin"] = False
        (tmp_path / "visual-asset-network.json").write_text(json.dumps(network))
    elif case == "missing_manifest_artifact":
        (tmp_path / "visual-manifest.json").unlink()
    elif case == "equal_snapshot_revision":
        values["moving_world"]["revision"] = values["initial_world"]["revision"]
    elif case == "backward_projected_at":
        values["moving_world"]["projectedAt"] = "2026-08-21T23:59:59Z"
    elif case == "missing_robot_1_custody":
        trajectory = json.loads((tmp_path / "world-trajectory.json").read_text())
        trajectory["samples"] = [
            sample
            for sample in trajectory["samples"]
            if sample.get("resources", {}).get("block:red-block", {}).get("owner") != "robot-1"
        ]
        (tmp_path / "world-trajectory.json").write_text(json.dumps(trajectory))
    elif case == "fabricated_evidence":
        events = json.loads((tmp_path / "events.json").read_text())
        events[0]["payload"]["harness"]["evidenceIds"][0] = "fabricated"
        (tmp_path / "events.json").write_text(json.dumps(events))
    elif case == "wrong_event_transition":
        events = json.loads((tmp_path / "events.json").read_text())
        events[0]["payload"]["resourceTransition"]["toFencingToken"] = 99
        (tmp_path / "events.json").write_text(json.dumps(events))
    elif case == "wrong_event_correlation":
        events = json.loads((tmp_path / "events.json").read_text())
        events[1]["correlationId"] = "task-other"
        (tmp_path / "events.json").write_text(json.dumps(events))
    elif case.startswith("wrong_observation_"):
        events = json.loads((tmp_path / "events.json").read_text())
        observation = events[0]["payload"]["harness"]["observations"][0]
        if case == "wrong_observation_id":
            observation["observationId"] = "robot-1/scene/999/1787356802000000000"
        elif case == "wrong_observation_source":
            observation["sourceId"] = "unregistered/scene"
        elif case == "wrong_observation_sequence":
            observation["sourceSequence"] = "999"
        elif case == "wrong_observation_time":
            observation["observedAt"] = "2026-08-22T00:00:01.999Z"
        elif case == "wrong_observation_frame":
            observation["frameId"] = "camera"
        else:
            observation["transformRevision"] = "forged"
        (tmp_path / "events.json").write_text(json.dumps(events))
    elif case in {"capture_nonce_substitution", "capture_public_key_replacement", "capture_signature_tamper"}:
        envelope_path = tmp_path / "capture-envelope.json"
        envelope = json.loads(envelope_path.read_text())
        if case == "capture_nonce_substitution":
            envelope["episodeNonce"] = "0" * 64
        elif case == "capture_public_key_replacement":
            envelope["publicKeyPem"] = envelope["publicKeyPem"].replace("A", "B", 1)
        else:
            envelope["signature"] = base64.b64encode(b"tampered").decode()
        envelope_path.write_text(json.dumps(envelope))
    elif case == "truncated_runner_network":
        network = json.loads((tmp_path / "visual-network.json").read_text())
        network["requests"].pop()
        network["observedRequestCount"] -= 1
        network["observedURLs"].pop()
        (tmp_path / "visual-network.json").write_text(json.dumps(network))

    summary = build_acceptance_summary(**values)

    assert summary["passed"] is False, case
    assert summary["checks"][failed_check] is False, (case, summary["checks"])


def test_public_asset_redirect_is_rejected_without_contacting_external_target(tmp_path):
    from tests.e2e.robocasa_harness import RoboCasaHandoffStack

    external_hits = []

    class External(BaseHTTPRequestHandler):
        def do_GET(self):
            external_hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"external")

        def log_message(self, _format, *_args):
            pass

    external = ThreadingHTTPServer(("127.0.0.1", 0), External)

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{external.server_port}/asset")
            self.end_headers()

        def log_message(self, _format, *_args):
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threads = [threading.Thread(target=server.serve_forever) for server in (external, origin)]
    for thread in threads:
        thread.start()
    stack = RoboCasaHandoffStack(tmp_path, origin.server_port, 1, 2, 3)
    try:
        with pytest.raises(AssertionError, match="redirect"):
            stack.public_bytes("/asset")
        assert external_hits == []
    finally:
        origin.shutdown()
        external.shutdown()
        for thread in threads:
            thread.join()


def test_manifest_rejects_external_reference_before_asset_io(tmp_path):
    from scripts.run_robocasa_harness import collect_visual_evidence

    class PublicStack:
        base_url = "http://127.0.0.1:18080"
        fetched = False

        def public_json(self, _path):
            return {
                "sceneAsset": "https://cdn.example/scene.glb",
                "robotModels": {
                    "xlerobot": {
                        "asset": "xlerobot.glb",
                        "binding": "xlerobot.binding.json",
                    }
                },
                "contentHashes": {},
            }

        def public_bytes(self, _url):
            self.fetched = True
            raise RuntimeError("must not perform asset I/O")

    stack = PublicStack()
    world = _world(1)
    with pytest.raises(AssertionError, match="origin"):
        collect_visual_evidence(
            stack,
            world,
            tmp_path,
            run_id=RUN_ID,
            task_id=TASK_ID,
            episode_nonce=EPISODE_NONCE,
        )
    assert stack.fetched is False


def _model_identity(world: dict) -> tuple[str, str]:
    identities = {
        (
            entity.get("attributes", {}).get("scene_id"),
            entity.get("attributes", {}).get("model_hash"),
        )
        for entity in world.get("entities", {}).values()
        if entity.get("attributes", {}).get("scene_id")
        or entity.get("attributes", {}).get("model_hash")
    }
    assert len(identities) == 1, identities
    scene_id, model_hash = identities.pop()
    assert isinstance(scene_id, str) and scene_id
    assert isinstance(model_hash, str) and len(model_hash) == 64
    return scene_id, model_hash


def _canonical_joints(world: dict, robot_id: str) -> dict[str, float]:
    joints = {
        key: value
        for key, value in world["robots"][robot_id].get("state", {}).items()
        if key.startswith("joint.")
    }
    assert len(joints) >= 12, (robot_id, joints)
    assert all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in joints.values()
    )
    return joints


def test_visual_manifest_and_glbs_match_the_live_world(
    robocasa_stack: RoboCasaHandoffStack,
):
    world = robocasa_stack.api("/v1/world")
    scene_id, model_hash = _model_identity(world)
    manifest_path = f"/assets/scenes/{scene_id}/manifest.json"
    manifest = robocasa_stack.public_json(manifest_path)

    assert manifest["sceneId"] == scene_id
    assert manifest["modelHash"] == model_hash
    manifest_url = robocasa_stack.base_url + manifest_path
    asset_urls = [
        urljoin(manifest_url, manifest["sceneAsset"]),
        urljoin(manifest_url, manifest["robotModels"]["xlerobot"]["asset"]),
    ]
    assert {urlsplit(url).netloc for url in asset_urls} == {
        urlsplit(robocasa_stack.base_url).netloc
    }

    for url in asset_urls:
        filename = urlsplit(url).path.rsplit("/", 1)[-1]
        payload = robocasa_stack.public_bytes(url)
        assert hashlib.sha256(payload).hexdigest() == manifest["contentHashes"][filename]


def test_canonical_joints_move_during_verified_chinese_handoff(
    robocasa_stack: RoboCasaHandoffStack,
):
    initial = robocasa_stack.api("/v1/world")
    initial_joints = {
        robot_id: _canonical_joints(initial, robot_id) for robot_id in ("robot-1", "robot-2")
    }

    task_id = robocasa_stack.create_and_approve()
    moving = robocasa_stack.wait_world(
        lambda world: (
            world.get("revision", 0) > initial["revision"]
            and any(
                abs(value - initial_joints[robot_id].get(key, 0.0)) > 1e-3
                for robot_id in ("robot-1", "robot-2")
                for key, value in _canonical_joints(world, robot_id).items()
            )
        )
    )
    assert moving["revision"] > initial["revision"]

    task = robocasa_stack.wait_task(task_id)
    assert task["state"] == "SUCCEEDED", robocasa_stack.log_tail()
    final = robocasa_stack.wait_world(
        lambda world: (
            world.get("entities", {}).get("red-block", {}).get("relations", {}).get("inside")
            == "right-target-zone"
        )
    )
    assert set(final["robots"]) >= {"robot-1", "robot-2"}
    for robot_id in ("robot-1", "robot-2"):
        _canonical_joints(final, robot_id)
    assert final["resources"]["block:red-block"]["owner"] == "environment"

    intents = robocasa_stack.api(f"/v1/tasks/{task_id}/intents")["intents"]
    assert [intent["harnessStatus"] for intent in intents] == [
        "SATISFIED",
        "SATISFIED",
    ]
