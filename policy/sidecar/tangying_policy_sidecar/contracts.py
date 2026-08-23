from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ActionBound(Contract):
    minimum: float
    maximum: float

    @model_validator(mode="after")
    def valid_range(self) -> ActionBound:
        if not math.isfinite(self.minimum) or not math.isfinite(self.maximum):
            raise ValueError("action bounds must be finite")
        if self.minimum > self.maximum:
            raise ValueError("action minimum exceeds maximum")
        return self


class TrainingMetadata(Contract):
    dataset: str = ""
    algorithm: str = ""
    seed: str = ""
    evaluation_pack: str = Field("", alias="evaluationPack")
    promoted_by: str = Field("", alias="promotedBy")


class PolicyManifest(Contract):
    schema_version: Literal["policy.manifest.v1"] = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1)
    version: str = Field(min_length=1)
    framework: Literal["vla", "imitation", "reinforcement", "deterministic"]
    artifact_sha256: str = Field(alias="artifactSha256")
    capabilities: list[str] = Field(min_length=1)
    robot_models: list[str] = Field(alias="robotModels")
    adapters: list[str]
    observation_schema: Literal["policy.observation.v1"] = Field(alias="observationSchema")
    required_observation_sources: list[str] = Field(alias="requiredObservationSources")
    max_observation_age_ms: int = Field(alias="maxObservationAgeMs", gt=0)
    action_schema: str = Field(alias="actionSchema", min_length=1)
    max_action_chunk_length: int = Field(alias="maxActionChunkLength", gt=0)
    action_bounds: dict[str, ActionBound] = Field(alias="actionBounds", min_length=1)
    transform_revision: str = Field("", alias="transformRevision")
    calibration_revision: str = Field("", alias="calibrationRevision")
    training: TrainingMetadata = Field(default_factory=TrainingMetadata)

    @model_validator(mode="after")
    def identified_artifact(self) -> PolicyManifest:
        if self.framework == "deterministic":
            valid = self.artifact_sha256.startswith("deterministic:")
        else:
            valid = re.fullmatch(r"[a-f0-9]{64}", self.artifact_sha256.lower()) is not None
        if not valid:
            raise ValueError("policy artifact identity is invalid")
        return self

    def revision(self) -> str:
        document = self.model_dump(by_alias=True)
        for name in ("capabilities", "robotModels", "adapters", "requiredObservationSources"):
            document[name] = sorted(set(document[name]))
        document["training"] = {
            key: value for key, value in document["training"].items() if value not in ("", None)
        }
        wire = json.dumps(
            _canonical_numbers(document), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        return hashlib.sha256(wire).hexdigest()


class ObservationSource(Contract):
    fresh: bool
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    anomalies: list[str] = Field(default_factory=list)


class FrameReference(Contract):
    uri: str
    mime: str
    sha256: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    observed_at: datetime = Field(alias="observedAt")


class Entity(Contract):
    entity_id: str = Field(alias="entityId", min_length=1)
    category: str = Field(min_length=1)
    attributes: dict[str, str] = Field(default_factory=dict)
    pose: list[float] = Field(default_factory=list)
    relations: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(0, ge=0, le=1, allow_inf_nan=False)

    @field_validator("pose")
    @classmethod
    def finite_pose(cls, values: list[float]) -> list[float]:
        if not all(math.isfinite(value) for value in values):
            raise ValueError("entity pose must be finite")
        return values


class ObservationBundle(Contract):
    schema_version: Literal["policy.observation.v1"] = Field(alias="schemaVersion")
    observation_id: str = Field(alias="observationId", min_length=1)
    observed_at: datetime = Field(alias="observedAt")
    robot_id: str = Field(alias="robotId", min_length=1)
    adapter: str
    robot_model: str = Field(alias="robotModel")
    transform_revision: str = Field(alias="transformRevision")
    calibration_revision: str = Field("", alias="calibrationRevision")
    sources: dict[str, ObservationSource]
    robot_state: dict[str, float] = Field(default_factory=dict, alias="robotState")
    entities: list[Entity] = Field(default_factory=list)
    frames: dict[str, FrameReference] = Field(default_factory=dict)


class InferenceRequest(Contract):
    schema_version: Literal["policy.inference.request.v1"] = Field(alias="schemaVersion")
    request_id: str = Field(alias="requestId", min_length=1)
    command_id: str = Field(alias="commandId", min_length=1)
    task_id: str = Field(alias="taskId", min_length=1)
    task_revision: int = Field(alias="taskRevision", ge=1)
    step_id: str = Field(alias="stepId")
    robot_id: str = Field(alias="robotId", min_length=1)
    capability: str = Field(min_length=1)
    target_ref: str = Field("", alias="targetRef")
    parameters: dict[str, Any] = Field(default_factory=dict)
    manifest_revision: str = Field(alias="manifestRevision", min_length=64, max_length=64)
    observation: ObservationBundle
    world_revision: int = Field(alias="worldRevision", ge=0)
    resource_id: str = Field("", alias="resourceId")
    fencing_token: int = Field(0, alias="fencingToken", ge=0)


class Action(Contract):
    values: dict[str, float] = Field(min_length=1)


class InferenceResult(Contract):
    schema_version: Literal["policy.inference.result.v1"] = Field(alias="schemaVersion")
    request_id: str = Field(alias="requestId", min_length=1)
    command_id: str = Field(alias="commandId", min_length=1)
    manifest_revision: str = Field(alias="manifestRevision", min_length=64, max_length=64)
    inference_id: str = Field(alias="inferenceId", min_length=1)
    observation_id: str = Field(alias="observationId", min_length=1)
    elapsed_ms: float = Field(0, alias="elapsedMs", ge=0, allow_inf_nan=False)
    actions: list[Action] = Field(min_length=1)
    diagnostics: list[str] = Field(default_factory=list)


def validate_result(
    manifest: PolicyManifest, request: InferenceRequest, result: InferenceResult
) -> None:
    if (
        request.manifest_revision != manifest.revision()
        or result.manifest_revision != request.manifest_revision
        or result.request_id != request.request_id
        or result.command_id != request.command_id
        or result.observation_id != request.observation.observation_id
    ):
        raise ValueError("inference identity mismatch")
    if len(result.actions) > manifest.max_action_chunk_length:
        raise ValueError("action chunk exceeds manifest limit")
    for action in result.actions:
        for name, value in action.values.items():
            bound = manifest.action_bounds.get(name)
            if (
                bound is None
                or not math.isfinite(value)
                or value < bound.minimum
                or value > bound.maximum
            ):
                raise ValueError(f"action {name!r} is outside manifest bounds")


def _canonical_numbers(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _canonical_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_numbers(item) for item in value]
    return value
