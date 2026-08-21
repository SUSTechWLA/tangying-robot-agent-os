"""Acceptance for the served RoboCasa visual twin and live joint state."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

import pytest

from tests.e2e.fleet_harness import HANDOFF_PROMPT

if TYPE_CHECKING:
    from tests.e2e.robocasa_harness import RoboCasaHandoffStack

pytest_plugins = ("tests.e2e.robocasa_harness",)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
RUN_ID = "0123456789abcdef0123456789abcdef"
TASK_ID = "task-review-1"
MODEL_HASH = "a" * 64


def _digest(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _world(revision: int, *, moved: bool = False, final: bool = False) -> dict:
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
            },
        },
        "robots": {
            "robot-1": {
                "robotId": "robot-1",
                "state": robot_1,
                "freshness": "FRESH",
                "activity": "IDLE",
            },
            "robot-2": {
                "robotId": "robot-2",
                "state": robot_2,
                "freshness": "FRESH",
                "activity": "IDLE",
            },
        },
        "resources": (
            {
                "block:red-block": {
                    "resourceId": "block:red-block",
                    "owner": "environment",
                    "fencingToken": 3,
                    "freshness": "FRESH",
                }
            }
            if final
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
        screenshot_path.write_bytes(PNG_1X1)
        snapshots[name] = {
            "path": f"visual/world-{name}.json",
            "revision": snapshot["revision"],
            "projectedAt": snapshot["projectedAt"],
            "digest": digest,
        }
        screenshots[name] = {
            "path": f"visual/{name}.png",
            "format": "png",
            "sha256": hashlib.sha256(PNG_1X1).hexdigest(),
            "bytes": len(PNG_1X1),
            "capturedAt": "2026-08-22T00:01:00Z",
            "worldRevision": snapshot["revision"],
            "worldDigest": digest,
            "worldStatus": "LIVE",
            "visualStatus": "DEGRADED" if name == "fallback" else "LIVE",
        }
    browser = {
        "schemaVersion": "tangying.browser-acceptance.v1",
        "runId": run_context["runId"],
        "taskId": run_context["taskId"],
        "request": HANDOFF_PROMPT,
        "adapter": "robocasa",
        "sceneId": "robocasa-handoff-v1",
        "contextDigest": _digest(run_context),
        "capturedAt": "2026-08-22T00:01:00Z",
        "snapshots": snapshots,
        "screenshots": screenshots,
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
                "pageUrl": "http://127.0.0.1:18080/",
                "baseOrigin": "http://127.0.0.1:18080",
                "observedRequestCount": 3,
                "observedURLs": [
                    "http://127.0.0.1:18080/app.js",
                    "http://127.0.0.1:18080/v1/world",
                    "http://127.0.0.1:18080/assets/scenes/robocasa-handoff-v1/scene.glb",
                ],
                "externalOrigins": [],
                "sameOrigin": True,
            }
        )
    )
    (tmp_path / "visual-performance.json").write_text(
        json.dumps(
            {
                "schemaVersion": "tangying.browser-performance.v1",
                "runId": run_context["runId"],
                "taskId": run_context["taskId"],
                "capturedAt": "2026-08-22T00:01:00Z",
                "browserMeasured": True,
                "firstInteractionMs": 500,
                "steadyFps": 60.0,
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
            }
        )
    )


def _valid_summary_inputs(tmp_path) -> dict:
    initial = _world(1)
    moving = _world(2, moved=True)
    final = _world(3, moved=True, final=True)
    run_context = {
        "schemaVersion": "tangying.robocasa-acceptance-run.v1",
        "runId": RUN_ID,
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
                "revision": 2,
                "projectedAt": moving["projectedAt"],
                "digest": _digest(moving),
            },
            "final": {
                "revision": 3,
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
                "taskId": TASK_ID,
                "baseOrigin": "http://127.0.0.1:18080",
                "requests": asset_requests,
                "externalOrigins": [],
                "sameOrigin": True,
                "publicAssetFetchMs": 12.0,
            }
        )
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
        "intents": [
            {
                "index": 0,
                "robotId": "robot-1",
                "status": "SUCCEEDED",
                "harnessStatus": "SATISFIED",
                "harnessReason": "PHYSICAL_POSTCONDITIONS_SATISFIED",
                "harnessEvidenceIds": ["evidence-r1-scene", "evidence-r1-proprioception"],
                "fencingToken": 1,
            },
            {
                "index": 1,
                "robotId": "robot-2",
                "status": "SUCCEEDED",
                "harnessStatus": "SATISFIED",
                "harnessReason": "PHYSICAL_POSTCONDITIONS_SATISFIED",
                "harnessEvidenceIds": ["evidence-r2-scene", "evidence-r2-proprioception"],
                "fencingToken": 2,
            },
        ],
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
    }


def test_acceptance_summary_requires_complete_provenance_bound_evidence(tmp_path):
    from scripts.run_robocasa_harness import build_acceptance_summary

    summary = build_acceptance_summary(**_valid_summary_inputs(tmp_path))

    assert summary["passed"] is True
    assert all(value is True for value in summary["checks"].values())
    assert summary["runId"] == RUN_ID


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
        ("jpeg_named_png", "screenshots"),
        ("corrupt_png", "screenshots"),
        ("old_browser_run", "provenance"),
        ("wrong_snapshot_digest", "provenance"),
        ("external_browser_origin", "browserNetwork"),
        ("slow_first_interaction", "browserPerformance"),
        ("low_steady_fps", "browserPerformance"),
        ("slow_refresh", "browserPerformance"),
        ("old_asset_run", "assetContentHashes"),
        ("external_asset_origin", "assetSameOrigin"),
        ("missing_manifest_artifact", "assetContentHashes"),
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
    elif case == "jpeg_named_png":
        (tmp_path / "visual/overview.png").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    elif case == "corrupt_png":
        (tmp_path / "visual/overview.png").write_bytes(PNG_1X1[:-8])
    elif case == "old_browser_run":
        browser = json.loads((tmp_path / "browser-evidence.json").read_text())
        browser["runId"] = "f" * 32
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
    elif case == "slow_first_interaction":
        performance = json.loads((tmp_path / "visual-performance.json").read_text())
        performance["firstInteractionMs"] = 5001
        (tmp_path / "visual-performance.json").write_text(json.dumps(performance))
    elif case == "low_steady_fps":
        performance = json.loads((tmp_path / "visual-performance.json").read_text())
        performance["steadyFps"] = 49.9
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
        collect_visual_evidence(stack, world, tmp_path, run_id=RUN_ID, task_id=TASK_ID)
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
