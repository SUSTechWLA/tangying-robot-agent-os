"""Acceptance for the served RoboCasa visual twin and live joint state."""

from __future__ import annotations

import hashlib
import json
import math
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

if TYPE_CHECKING:
    from tests.e2e.robocasa_harness import RoboCasaHandoffStack

pytest_plugins = ("tests.e2e.robocasa_harness",)


def test_visual_network_artifact_preserves_browser_capture(tmp_path):
    from scripts.run_robocasa_harness import collect_visual_evidence

    scene = b"scene"
    robot = b"robot"
    binding = b"binding"
    manifest = {
        "sceneAsset": "scene.glb",
        "robotModels": {"xlerobot": {"asset": "xlerobot.glb", "binding": "xlerobot.binding.json"}},
        "contentHashes": {
            "scene.glb": hashlib.sha256(scene).hexdigest(),
            "xlerobot.glb": hashlib.sha256(robot).hexdigest(),
            "xlerobot.binding.json": hashlib.sha256(binding).hexdigest(),
        },
    }

    class PublicStack:
        base_url = "http://127.0.0.1:18080"

        def public_json(self, _path):
            return manifest

        def public_bytes(self, url):
            return {
                "scene.glb": scene,
                "xlerobot.glb": robot,
                "xlerobot.binding.json": binding,
            }[url.rsplit("/", 1)[-1]]

    browser_capture = {
        "pageUrl": "http://127.0.0.1:18080/",
        "observedRequestCount": 42,
        "externalOrigins": [],
        "sameOrigin": True,
    }
    (tmp_path / "visual-network.json").write_text(json.dumps(browser_capture))
    world = {
        "entities": {
            "robot-1": {
                "attributes": {
                    "scene_id": "robocasa-handoff-v1",
                    "model_hash": "a" * 64,
                }
            }
        }
    }

    _, visual = collect_visual_evidence(PublicStack(), world, tmp_path)
    artifact = json.loads((tmp_path / "visual-network.json").read_text())

    assert visual["sameOrigin"] is True
    assert artifact["browserCapture"] == browser_capture


def test_acceptance_summary_fails_closed_over_every_visual_world_boundary():
    from scripts.run_robocasa_harness import build_acceptance_summary

    joints = {f"joint.arm.{index}": float(index) for index in range(12)}
    world = {
        "entities": {
            "robot-1": {
                "attributes": {
                    "scene_id": "robocasa-handoff-v1",
                    "model_hash": "a" * 64,
                    "adapter": "robocasa",
                }
            },
            "red-block": {"relations": {"inside": "right-target-zone"}},
        },
        "robots": {
            "robot-1": {"state": joints},
            "robot-2": {"state": joints},
        },
        "resources": {"block:red-block": {"owner": "environment"}},
    }
    manifest = {"sceneId": "robocasa-handoff-v1", "modelHash": "a" * 64}
    verdicts = [{"status": "SATISFIED"}, {"status": "SATISFIED"}]
    visual = {
        "assetHashesMatch": True,
        "sameOrigin": True,
        "files": {
            "manifest": "visual-manifest.json",
            "network": "visual-network.json",
            "performance": "visual-performance.json",
        },
        "screenshots": {
            "overview": "visual/overview.png",
            "robot-1": "visual/robot-1.png",
            "robot-2": "visual/robot-2.png",
            "handoff-final": "visual/handoff-final.png",
            "fallback": "visual/fallback.png",
        },
    }

    summary = build_acceptance_summary(
        task_id="task-1",
        task={"state": "SUCCEEDED"},
        world=world,
        manifest=manifest,
        verdicts=verdicts,
        visual=visual,
    )

    assert summary["passed"] is True
    assert summary["checks"] == {
        "assetContentHashesMatch": True,
        "canonicalJointCounts": {"robot-1": 12, "robot-2": 12},
        "finalPlacement": "right-target-zone",
        "harnessStatuses": ["SATISFIED", "SATISFIED"],
        "modelHashMatches": True,
        "resourceOwner": "environment",
        "robotIds": ["robot-1", "robot-2"],
        "sameOriginAssets": True,
        "sceneIdMatches": True,
        "taskState": "SUCCEEDED",
    }

    world["resources"]["block:red-block"]["owner"] = "robot-2"
    failed = build_acceptance_summary(
        task_id="task-1",
        task={"state": "SUCCEEDED"},
        world=world,
        manifest=manifest,
        verdicts=verdicts,
        visual=visual,
    )
    assert failed["passed"] is False


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
