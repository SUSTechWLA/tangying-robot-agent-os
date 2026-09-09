"""Versioned, hardware-independent contracts at the trusted adapter boundary.

Providers must transform sensor measurements into world metres before returning
a reconstruction. Validation does not perform calibration, SLAM or reconstruction.
Quaternion order is always w, x, y, z.
"""

from __future__ import annotations

import math
import re
import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

Identifier = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^\S+$")]
SourceType = Literal[
    "sim_ground_truth", "robot_proprioception", "rgbd_camera", "lidar", "slam_pose",
    "tool_result_evidence", "operator_annotation", "sensor_fusion", "stereo_camera",
    "monocular_camera",
]
Embodiment = Literal["arm", "dual_arm", "mobile_manipulator", "mobile_base", "sensor_rig", "custom"]
CANONICAL_TOOLS = frozenset({
    "observe_scene", "resolve_targets", "plan_grasp", "manipulation.pick", "verify_grasp",
    "manipulation.place", "verify_placement", "verify_arrival", "recover_to_safe_pose", "emergency_stop",
    "navigation.navigate", "arm.move",
})
PHYSICAL_TOOLS = frozenset({
    "manipulation.pick", "manipulation.place", "recover_to_safe_pose", "emergency_stop",
    "navigation.navigate", "arm.move",
})


class Contract(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid",
        strict=True, allow_inf_nan=False, validate_assignment=True,
        revalidate_instances="always",
    )

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class Joint(Contract):
    name: Identifier
    kind: Literal["revolute", "prismatic", "continuous", "fixed"]
    unit: Literal["rad", "m"]
    lower: float
    upper: float

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.lower > self.upper:
            raise ValueError("joint lower limit exceeds upper limit")
        if self.kind in ("revolute", "continuous") and self.unit != "rad":
            raise ValueError("angular joints use radians")
        if self.kind == "prismatic" and self.unit != "m":
            raise ValueError("prismatic joints use metres")
        return self


class EndEffector(Contract):
    id: Identifier
    kind: Literal["gripper", "suction", "tool", "sensor", "custom"]
    joint_names: list[Identifier] = Field(default_factory=list, max_length=128)


class Sensor(Contract):
    source_id: Identifier
    source_type: SourceType
    frame_id: Identifier
    transform_revision: Identifier
    max_age_ms: int = Field(gt=0, le=60_000)


class ActionLimit(Contract):
    min: float
    max: float
    unit: Literal["rad", "m", "rad/s", "m/s", "normalized", "N"]

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.min > self.max:
            raise ValueError("action minimum exceeds maximum")
        return self


class RobotProfile(Contract):
    schema_version: Literal["robot.profile.v1"]
    robot_id: Identifier
    adapter_id: Identifier
    adapter_version: Identifier
    model_id: Identifier
    embodiment: Embodiment
    joints: list[Joint] = Field(max_length=128)
    end_effectors: list[EndEffector] = Field(max_length=32)
    sensors: list[Sensor] = Field(min_length=1, max_length=64)
    action_limits: dict[Identifier, ActionLimit] = Field(max_length=256)
    tools: list[Identifier] = Field(min_length=2, max_length=len(CANONICAL_TOOLS))

    @model_validator(mode="after")
    def consistent(self):
        _unique([item.name for item in self.joints], "joint names")
        _unique([item.id for item in self.end_effectors], "end effector IDs")
        _unique([item.source_id for item in self.sensors], "sensor sources")
        _unique(self.tools, "tool names")
        joint_names = {item.name for item in self.joints}
        for effector in self.end_effectors:
            _unique(effector.joint_names, "end effector joint references")
            if not set(effector.joint_names) <= joint_names:
                raise ValueError("end effector references unknown joint")
        if not set(self.tools) <= CANONICAL_TOOLS:
            raise ValueError("profile contains an unrecognized canonical tool")
        if not {"observe_scene", "emergency_stop"} <= set(self.tools):
            raise ValueError("every profile must implement observation and emergency stop")
        action_tools = {"arm.move", "manipulation.pick", "manipulation.place", "recover_to_safe_pose"}
        if set(self.tools) & action_tools and not self.action_limits:
            raise ValueError("action tools require explicit actuator limits")
        if "navigation.navigate" in self.tools:
            for axis in "xyz":
                limit = self.action_limits.get(f"navigation.{axis}")
                if limit is None or limit.unit != "m":
                    raise ValueError("navigation requires navigation.x/y/z workspace limits in metres")
        return self


def _unique(values: list[str], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate {label}")


def validate_pose(pose: list[float]) -> list[float]:
    if len(pose) != 7 or not all(math.isfinite(value) for value in pose):
        raise ValueError("world pose must contain seven finite numbers")
    if abs(sum(value * value for value in pose[3:]) - 1.0) > 1e-3:
        raise ValueError("world pose quaternion must be normalized (w,x,y,z)")
    return pose


class Entity(Contract):
    entity_id: Identifier
    category: Identifier
    attributes: dict[Identifier, Annotated[str, Field(max_length=1024)]] = Field(default_factory=dict, max_length=64)
    pose: list[float] = Field(min_length=7, max_length=7)
    confidence: float = Field(ge=0, le=1)
    relation: str = Field(default="", max_length=512)

    _pose = field_validator("pose")(validate_pose)


class Reconstruction(Contract):
    schema_version: Literal["scene.reconstruction.v1"]
    robot_id: Identifier
    observation_id: Identifier
    source_id: Identifier
    source_type: SourceType
    source_frame_id: Identifier
    frame_id: Literal["world"]
    transform_revision: Identifier
    observed_at_unix_ms: int = Field(gt=0)
    sequence: int = Field(gt=0, le=9_007_199_254_740_991)
    units: Literal["m"]
    entities: list[Entity] = Field(default_factory=list, max_length=2048)
    points: list[Annotated[list[float], Field(min_length=3, max_length=3)]] = Field(
        default_factory=list, max_length=4096,
    )
    point_colors: list[
        Annotated[list[Annotated[int, Field(ge=0, le=255)]], Field(min_length=3, max_length=3)]
    ] = Field(default_factory=list, max_length=4096)

    @model_validator(mode="after")
    def unique_entities(self):
        _unique([entity.entity_id for entity in self.entities], "scene entity IDs")
        if self.point_colors and len(self.point_colors) != len(self.points):
            raise ValueError("pointColors must contain one RGB triple for every point")
        return self


def validate_reconstruction(
    value: dict[str, Any] | Reconstruction,
    profile: dict[str, Any] | RobotProfile,
    *, now_ms: int | None = None,
) -> Reconstruction:
    profile = RobotProfile.model_validate(profile)
    result = Reconstruction.model_validate(value)
    if result.robot_id != profile.robot_id:
        raise ValueError("reconstruction robot identity does not match profile")
    sensor = next((item for item in profile.sensors if item.source_id == result.source_id), None)
    if sensor is None:
        raise ValueError("reconstruction source is not declared by profile")
    if (result.source_type != sensor.source_type or result.source_frame_id != sensor.frame_id
            or result.transform_revision != sensor.transform_revision):
        raise ValueError("reconstruction sensor frame/type/calibration does not match profile")
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    age = now_ms - result.observed_at_unix_ms
    if age < -250 or age > sensor.max_age_ms:
        raise ValueError("reconstruction is stale or dated in the future")
    return result


class ObserveParameters(Contract):
    streams: list[str] = Field(default_factory=list, max_length=64)
    max_rate_hz: int = Field(default=1, alias="max_rate_hz", ge=1, le=120)

    @field_validator("max_rate_hz", mode="before")
    @classmethod
    def struct_integer(cls, value):
        # Protobuf Struct deliberately encodes every JSON number as double.
        # Accept its exact integer representation without accepting strings,
        # booleans or truncating fractions.
        if type(value) is float and math.isfinite(value) and value.is_integer():
            return int(value)
        return value


class ResolveParameters(Contract):
    object_id: Identifier
    destination_id: Identifier
    object_confidence: float | None = Field(default=None, ge=0, le=1)
    destination_confidence: float | None = Field(default=None, ge=0, le=1)


class GraspParameters(Contract):
    object_id: Identifier
    destination_id: Identifier
    keep_upright: bool = False


class PolicyExecution(Contract):
    policy_id: Identifier
    policy_version: Identifier
    framework: Literal["deterministic", "vla", "imitation", "reinforcement"]
    artifact_sha256: Identifier
    manifest_revision: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    inference_id: Identifier
    observation_id: Identifier
    elapsed_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def valid_artifact(self):
        if self.framework == "deterministic":
            valid = self.artifact_sha256.startswith("deterministic:")
        else:
            valid = re.fullmatch(r"[a-fA-F0-9]{64}", self.artifact_sha256) is not None
        if not valid:
            raise ValueError("policy artifact identity does not match its framework")
        return self


class ActionParameters(Contract):
    action_chunk: list[dict[Identifier, float]] = Field(
        alias="action_chunk", min_length=1, max_length=64,
    )
    keep_upright: bool = False
    target_ref: Identifier | None = None
    policy_execution: PolicyExecution | None = Field(default=None, alias="policy_execution")


class VerifyGraspParameters(Contract):
    object_id: Identifier


class VerifyPlacementParameters(VerifyGraspParameters):
    destination_id: Identifier


class VerifyArrivalParameters(Contract):
    goal_pose: list[float] = Field(min_length=7, max_length=7)

    _pose = field_validator("goal_pose")(validate_pose)


class NavigationParameters(Contract):
    goal_pose: list[float] = Field(min_length=7, max_length=7)

    _pose = field_validator("goal_pose")(validate_pose)


class StopParameters(Contract):
    reason: str = Field(default="", max_length=1024)


TOOL_PARAMETERS: dict[str, type[Contract]] = {
    "observe_scene": ObserveParameters, "resolve_targets": ResolveParameters,
    "plan_grasp": GraspParameters, "manipulation.pick": ActionParameters,
    "manipulation.place": ActionParameters, "verify_grasp": VerifyGraspParameters,
    "verify_placement": VerifyPlacementParameters, "verify_arrival": VerifyArrivalParameters,
    "recover_to_safe_pose": ActionParameters, "arm.move": ActionParameters,
    "navigation.navigate": NavigationParameters,
    "emergency_stop": StopParameters,
}


def validate_tool_parameters(
    name: str, values: dict[str, Any], profile: RobotProfile, *, target_ref: str | None = None,
) -> None:
    if name not in profile.tools:
        raise ValueError("tool is not declared by robot profile")
    parameters = TOOL_PARAMETERS[name].model_validate(values)
    if isinstance(parameters, ActionParameters):
        # Existing Agent plans retain targetRef in parameters while also
        # materializing Command.target_ref. Accept the duplicate only when
        # both representations identify the same target.
        if parameters.target_ref is not None and parameters.target_ref != target_ref:
            raise ValueError("parameter targetRef does not match command target_ref")
        if (parameters.policy_execution is not None
                and parameters.policy_execution.framework == "deterministic"
                and any(sensor.source_type != "sim_ground_truth" for sensor in profile.sensors)):
            raise ValueError("deterministic policy execution is limited to simulation profiles")
        for action in parameters.action_chunk:
            if not action:
                raise ValueError("action cannot be empty")
            for key, value in action.items():
                limit = profile.action_limits.get(key)
                if limit is None or not limit.min <= value <= limit.max:
                    raise ValueError(f"action {key!r} is not within registered actuator limits")
    if isinstance(parameters, NavigationParameters):
        for axis, value in zip("xyz", parameters.goal_pose[:3], strict=True):
            limit = profile.action_limits[f"navigation.{axis}"]
            if not limit.min <= value <= limit.max:
                raise ValueError(f"navigation goal {axis} is outside the commissioned workspace")


def contract_schemas() -> dict[str, Any]:
    """Machine-readable onboarding schemas; no provider code is imported."""
    return {
        "robot.profile.v1": RobotProfile.model_json_schema(by_alias=True),
        "scene.reconstruction.v1": Reconstruction.model_json_schema(by_alias=True),
        "tools": {name: model.model_json_schema(by_alias=True) for name, model in TOOL_PARAMETERS.items()},
    }
