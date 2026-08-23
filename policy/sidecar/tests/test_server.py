from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tangying_policy_sidecar.contracts import PolicyManifest
from tangying_policy_sidecar.providers import CallableProvider
from tangying_policy_sidecar.server import serve_in_thread


def manifest_document() -> dict[str, object]:
    return {
        "schemaVersion": "policy.manifest.v1",
        "policyId": "test-policy",
        "version": "1",
        "framework": "deterministic",
        "artifactSha256": "deterministic:test-policy-v1",
        "capabilities": ["manipulation.pick", "manipulation.place"],
        "robotModels": ["xlerobot-sim"],
        "adapters": ["mujoco"],
        "observationSchema": "policy.observation.v1",
        "requiredObservationSources": ["scene", "proprioception"],
        "maxObservationAgeMs": 5000,
        "actionSchema": "xlerobot.named-joints.v1",
        "maxActionChunkLength": 8,
        "actionBounds": {"left_arm_gripper.pos": {"minimum": 0, "maximum": 100}},
        "transformRevision": "mujoco-world-v1",
    }


def request_document(manifest: PolicyManifest) -> dict[str, object]:
    return {
        "schemaVersion": "policy.inference.request.v1",
        "requestId": "request-1",
        "commandId": "command-1",
        "taskId": "task-1",
        "taskRevision": 1,
        "stepId": "sender",
        "robotId": "robot-1",
        "capability": "manipulation.pick",
        "targetRef": "red-block",
        "manifestRevision": manifest.revision(),
        "observation": {
            "schemaVersion": "policy.observation.v1",
            "observationId": "observation-1",
            "observedAt": "2026-08-24T12:00:00Z",
            "robotId": "robot-1",
            "adapter": "mujoco",
            "robotModel": "xlerobot-sim",
            "transformRevision": "mujoco-world-v1",
            "sources": {
                "scene": {"fresh": True, "confidence": 1},
                "proprioception": {"fresh": True, "confidence": 1},
            },
        },
        "worldRevision": 4,
        "resourceId": "block:red-block",
        "fencingToken": 2,
    }


def read_json(url: str) -> dict[str, object]:
    with urlopen(url, timeout=2) as response:
        return json.load(response)


def test_sidecar_exposes_health_manifest_and_inference_protocol():
    manifest = PolicyManifest.model_validate(manifest_document())
    provider = CallableProvider(manifest, lambda current: [{"left_arm_gripper.pos": 50}])
    server, thread = serve_in_thread(provider, host="127.0.0.1", port=0)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        assert read_json(base_url + "/healthz") == {"status": "ok"}
        assert read_json(base_url + "/v1/manifest")["policyId"] == "test-policy"
        body = json.dumps(request_document(manifest)).encode()
        request = Request(
            base_url + "/v1/infer",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            result = json.load(response)
        assert result["schemaVersion"] == "policy.inference.result.v1"
        assert result["commandId"] == "command-1"
        assert result["actions"] == [{"values": {"left_arm_gripper.pos": 50.0}}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sidecar_returns_bounded_public_error_without_echoing_request_body():
    manifest = PolicyManifest.model_validate(manifest_document())
    provider = CallableProvider(manifest, lambda current: [{"left_arm_gripper.pos": 50}])
    server, thread = serve_in_thread(provider, host="127.0.0.1", port=0, max_request_bytes=32)
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/v1/infer",
            data=b'{"bearerToken":"secret-value-that-must-not-be-returned"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request, timeout=2)
        except HTTPError as error:
            wire = error.read().decode()
            assert error.code == 413
            assert "secret-value" not in wire
            assert json.loads(wire)["code"] == "REQUEST_TOO_LARGE"
        else:
            raise AssertionError("oversized request unexpectedly succeeded")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
