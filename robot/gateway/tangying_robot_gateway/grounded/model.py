"""GCL wire models. No model-generated expression is executed as Python."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Verdict = Literal["VERIFIED", "FALSIFIED", "UNKNOWN"]
Scalar = bool | int | float | str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, strict=True)


class EvidenceRef(StrictModel):
    uri: str = Field(min_length=1, pattern=r"^evidence://[a-zA-Z0-9_.-]+/[a-f0-9]{64}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal[
        "rgb", "depth", "detection", "point_cloud", "gripper", "force", "pose", "tool_return"
    ]
    inline_summary: dict[str, Scalar] = Field(default_factory=dict)


class EvidenceSample(StrictModel):
    sample_id: str = Field(min_length=1)
    edge_boot_id: str = Field(min_length=1)
    edge_monotonic_ts_ns: int = Field(ge=0)
    action_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    object_id: str = ""
    confidence: float = Field(ge=0, le=1)
    values: dict[str, Scalar]
    evidence_refs: list[EvidenceRef]
    record_ref: EvidenceRef | None = None


class Expression(StrictModel):
    op: Literal["predicate", "all", "any", "not", "within", "stable", "unchanged"] = "predicate"
    predicate: str = ""
    args: dict[str, Scalar] = Field(default_factory=dict)
    children: list[Expression] = Field(default_factory=list)
    frames: int = Field(default=3, ge=1, le=1000)
    duration_s: float = Field(default=1.0, gt=0, le=60)

    @model_validator(mode="after")
    def structure(self):
        if self.op == "predicate":
            if not self.predicate or self.children:
                raise ValueError("a predicate needs a name and no children")
        elif self.predicate or (
            self.op in {"not", "within", "stable", "unchanged"} and len(self.children) != 1
        ):
            raise ValueError("invalid temporal/boolean expression")
        elif not self.children:
            raise ValueError("empty boolean expressions are forbidden")
        return self


class ActionContract(StrictModel):
    schema_version: Literal["gcl.v1"] = "gcl.v1"
    name: str = Field(min_length=1)
    extends: list[str] = Field(default_factory=list)
    preconditions: list[Expression] = Field(default_factory=list)
    postconditions: list[Expression]
    invariants: list[Expression] = Field(default_factory=list)
    safety_constraints: list[Expression] = Field(default_factory=list)
    timeout_s: float = Field(default=30.0, gt=0, le=600)
    window_s: float = Field(default=0.5, gt=0, le=60)
    max_gap_s: float = Field(default=0.25, gt=0, le=10)
    min_confidence: float = Field(default=0.8, gt=0, le=1)


class StateReport(StrictModel):
    report_id: str
    schema_version: Literal["state-report.v1"] = "state-report.v1"
    task_id: str
    episode_id: str
    stage_id: str
    action_id: str
    action_name: str
    action_params: dict[str, JsonValue]
    tool_return_status: str
    verdict: Verdict
    failure_type: str
    failure_class: str
    confidence: float = Field(ge=0, le=1)
    evidence_completeness: float = Field(ge=0, le=1)
    evidence_refs: list[EvidenceRef]
    verified_facts: list[str]
    falsified_facts: list[str]
    unknowns: list[str]
    suggested_recovery: list[str]
    forbidden_actions: list[str]
    requires_human: bool
    edge_boot_id: str
    edge_monotonic_ts_ns: int = Field(ge=0)
    logical_clock: int = Field(ge=0)
    arrival_ts: str
    verifier_version: str = "gvf-1.0.0"


def canonical(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=lambda item: (
            item.model_dump(mode="json")
            if isinstance(item, BaseModel)
            else (_ for _ in ()).throw(TypeError(f"unsupported value: {type(item).__name__}"))
        ),
    )


def resolve_contract(name: str, catalog: dict[str, ActionContract], stack=()) -> ActionContract:
    if name in stack:
        raise ValueError("cyclic contract inheritance")
    contract = catalog[name]
    parents = [resolve_contract(p, catalog, (*stack, name)) for p in contract.extends]
    data = contract.model_dump()
    for key in ("preconditions", "postconditions", "invariants", "safety_constraints"):
        data[key] = [e.model_dump() for p in parents for e in getattr(p, key)] + data[key]
    # A child may strengthen, never relax, an inherited safety budget.
    for key in ("timeout_s", "window_s", "max_gap_s"):
        data[key] = min([getattr(contract, key)] + [getattr(p, key) for p in parents])
    data["min_confidence"] = max([contract.min_confidence] + [p.min_confidence for p in parents])
    data["extends"] = []
    return ActionContract.model_validate(data)


def validate_dag(nodes: dict[str, str], dependencies: dict[str, list[str]], catalog):
    visited, active = set(), set()

    def visit(node):
        if node not in nodes or node in active:
            raise ValueError("unknown node or cyclic task DAG")
        if node in visited:
            return
        active.add(node)
        for parent in dependencies.get(node, []):
            visit(parent)
        resolve_contract(nodes[node], catalog)
        active.remove(node)
        visited.add(node)

    if set(dependencies) - set(nodes):
        raise ValueError("unknown dependency node")
    for node in nodes:
        visit(node)
    return True
