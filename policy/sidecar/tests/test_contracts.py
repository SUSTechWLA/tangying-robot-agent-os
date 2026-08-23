from __future__ import annotations

import math

import pytest
from pydantic import ValidationError
from tangying_policy_sidecar.contracts import (
    InferenceRequest,
    PolicyManifest,
    validate_result,
)
from tangying_policy_sidecar.providers import CallableProvider


def manifest_document(framework: str = "vla") -> dict[str, object]:
    artifact = "a" * 64 if framework != "deterministic" else "deterministic:test-policy-v1"
    return {
        "schemaVersion": "policy.manifest.v1",
        "policyId": "test-policy",
        "version": "1",
        "framework": framework,
        "artifactSha256": artifact,
        "capabilities": ["manipulation.pick", "manipulation.place"],
        "robotModels": ["xlerobot-sim"],
        "adapters": ["mujoco"],
        "observationSchema": "policy.observation.v1",
        "requiredObservationSources": ["scene", "proprioception"],
        "maxObservationAgeMs": 5000,
        "actionSchema": "xlerobot.named-joints.v1",
        "maxActionChunkLength": 8,
        "actionBounds": {
            "left_arm_gripper.pos": {"minimum": 0, "maximum": 100},
            "left_arm_shoulder.pos": {"minimum": -12, "maximum": 12},
        },
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


def test_manifest_revision_is_canonical_and_learned_artifact_is_identified():
    manifest = PolicyManifest.model_validate(manifest_document())
    first = manifest.revision()
    reordered = manifest_document()
    reordered["capabilities"] = ["manipulation.place", "manipulation.pick"]
    assert PolicyManifest.model_validate(reordered).revision() == first
    assert len(first) == 64

    invalid = manifest_document()
    invalid["artifactSha256"] = "unsigned-model"
    with pytest.raises(ValidationError):
        PolicyManifest.model_validate(invalid)


def test_manifest_revision_matches_go_edge_contract():
    document = manifest_document()
    document.update(
        {
            "policyId": "xlerobot-tabletop-vla",
            "version": "2026.08.1",
            "artifactSha256": "4f7b2f76c8f6018f6f29f76f4ec7d9910d413458a0f048cf94b6df7e15851508",
            "robotModels": ["xlerobot-dual-arm"],
            "adapters": ["xlerobot_direct"],
            "maxObservationAgeMs": 500,
            "maxActionChunkLength": 32,
            "transformRevision": "lab-map-v3",
            "calibrationRevision": "xlerobot-01-cal-v7",
        }
    )
    assert PolicyManifest.model_validate(document).revision() == (
        "166ec1d18312132c11fd3650facaafea0615598081e5a3a6c117cb0c4eed09e8"
    )


def test_callable_provider_supports_vla_il_and_rl_without_framework_dependency():
    for framework in ("vla", "imitation", "reinforcement"):
        manifest = PolicyManifest.model_validate(manifest_document(framework))
        request = InferenceRequest.model_validate(request_document(manifest))
        provider = CallableProvider(
            manifest,
            lambda current: [{"left_arm_gripper.pos": 50}],
        )
        result = provider.infer(request)
        assert result.command_id == request.command_id
        assert result.observation_id == request.observation.observation_id
        validate_result(manifest, request, result)


@pytest.mark.parametrize("value", [math.nan, math.inf, -1, 101])
def test_provider_rejects_non_finite_or_out_of_bound_actions(value: float):
    manifest = PolicyManifest.model_validate(manifest_document())
    request = InferenceRequest.model_validate(request_document(manifest))
    provider = CallableProvider(manifest, lambda current: [{"left_arm_gripper.pos": value}])
    with pytest.raises(ValueError, match="action"):
        provider.infer(request)
