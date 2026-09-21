import json
import time

import pytest
from pydantic import ValidationError
from tangying_robot_gateway.grounded import (
    Expression,
    RuntimeVerifier,
    load_contracts,
    render_report,
    validate_render,
)
from tangying_robot_gateway.grounded.model import (
    ActionContract,
    EvidenceRef,
    StateReport,
    resolve_contract,
    validate_dag,
)
from tangying_robot_gateway.grounded.runtime import GroundedRuntime
from tangying_robot_gateway.grounded.store import EvidenceStore
from tangying_robot_gateway.runtime import Command, Result


def samples(
    store,
    *,
    values=None,
    n=3,
    confidence=0.99,
    start=100,
    step=100_000_000,
    action_id="action",
    boot="boot",
    modalities=None,
):
    defaults = {
        "gripper_closed": True,
        "load_n": 0.5,
        "held_object_id": "cup",
        "height_above_surface_m": 0.12,
        "displacement_m": 0.001,
        "position_error_m": 0.01,
        "yaw_error_rad": 0.01,
        "inside_container": True,
        "container_id": "tray",
        "collision_force_n": 0.0,
    }
    defaults.update(values or {})
    # Deliberately synthetic unit-test bytes, never used as simulation evidence.
    refs = [
        store.put(f"测试证据:{k}".encode(), k)
        for k in (modalities or ["rgb", "depth", "detection", "gripper", "force", "pose"])
    ]
    return [
        store.record_sample(
            sample_id=f"sample-{i}",
            edge_boot_id=boot,
            edge_monotonic_ts_ns=start + (i + 1) * step,
            action_id=action_id,
            source_id="test",
            object_id="cup",
            confidence=confidence,
            values=defaults,
            evidence_refs=refs,
        )
        for i in range(n)
    ]


def report(store, stream, name="manipulation.pick", **extra):
    return RuntimeVerifier(store.exists).verify(
        load_contracts()[name],
        stream,
        action_id="action",
        edge_boot_id="boot",
        start_ns=100,
        end_ns=500_000_100,
        params={"object": "cup", "container": "tray"},
        **extra,
    )


@pytest.mark.parametrize(
    "name,values,verdict,failure",
    [
        ("manipulation.pick", {}, "VERIFIED", "NONE"),
        (
            "manipulation.pick",
            {"load_n": 0.0, "height_above_surface_m": 0.0, "held_object_id": ""},
            "FALSIFIED",
            "GRASP_MISS",
        ),
        ("manipulation.pick", {"held_object_id": "plate"}, "FALSIFIED", "WRONG_OBJECT"),
        ("manipulation.place", {"gripper_closed": False}, "VERIFIED", "NONE"),
        (
            "manipulation.place",
            {"displacement_m": 0.1, "gripper_closed": False},
            "FALSIFIED",
            "PLACE_UNSTABLE",
        ),
        ("navigation.navigate", {}, "VERIFIED", "NONE"),
        ("navigation.navigate", {"position_error_m": 0.4}, "FALSIFIED", "NAV_NOT_REACHED"),
    ],
)
def test_core_predicates(tmp_path, name, values, verdict, failure):
    store = EvidenceStore(tmp_path)
    r = report(store, samples(store, values=values), name)
    assert (r.verdict, r.failure_type) == (verdict, failure)
    assert r.tool_return_status == "SUCCESS"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"n": 2},
        {"boot": "old-boot"},
        {"action_id": "other"},
        {"confidence": 0.3},
        {"start": -100_000_000},
        {"step": 400_000_000},
        {"modalities": ["rgb", "depth", "detection"]},
        {"values": {"occluded": True}},
    ],
)
def test_unavailable_evidence_never_passes(tmp_path, kwargs):
    store = EvidenceStore(tmp_path)
    r = report(store, samples(store, **kwargs))
    assert r.verdict == "UNKNOWN"
    assert r.failure_class == "UNKNOWN_OUTCOME"
    assert "自动重试硬件动作" in r.forbidden_actions


def test_duplicate_and_tampered_samples_are_not_three_frames(tmp_path):
    store = EvidenceStore(tmp_path)
    frames = samples(store)
    assert report(store, [frames[0]] * 3).verdict == "UNKNOWN"
    altered = [f.model_copy(update={"values": dict(f.values, load_n=10.0)}) for f in frames]
    assert report(store, altered).verdict == "UNKNOWN"
    (store.root / "blobs" / frames[0].evidence_refs[0].sha256).write_bytes(b"corrupted")
    assert report(store, frames).verdict == "UNKNOWN"


def test_temporal_slip_and_renderer_drift(tmp_path):
    store = EvidenceStore(tmp_path)
    frames = samples(store)
    frames[-1] = samples(
        store, n=1, start=200_000_100, values={"load_n": 0.0, "displacement_m": 0.1}
    )[0].model_copy(update={"sample_id": "slip"})
    # Re-record after changing identity; the immutable manifest must agree.
    frames[-1] = store.record_sample(**frames[-1].model_dump(exclude={"record_ref"}))
    r = report(store, frames)
    assert r.failure_type == "GRASP_SLIP"
    assert render_report(r) == render_report(StateReport.model_validate_json(r.model_dump_json()))
    assert validate_render(r, render_report(r))
    assert not validate_render(r, render_report(r) + "\n杯子已拿起")


def test_duration_window_and_unknown_negation(tmp_path):
    store = EvidenceStore(tmp_path)
    verifier = RuntimeVerifier(store.exists)
    contract = load_contracts()["manipulation.pick"]
    expr = Expression(
        op="unchanged",
        duration_s=1.0,
        children=[Expression(predicate="Stable", args={"object": "cup"})],
    )
    assert (
        verifier.evaluate(expr, samples(store), {}, contract, 100, 500_000_100).verdict == "UNKNOWN"
    )
    stream = samples(store, n=10)
    assert verifier.evaluate(expr, stream, {}, contract, 100, 1_000_000_100).verdict == "VERIFIED"
    expr = Expression(op="within", duration_s=0.5, children=[Expression(predicate="At")])
    stream = samples(store, values={"position_error_m": 1.0})
    assert verifier.evaluate(expr, stream, {}, contract, 100, 300_000_100).verdict == "UNKNOWN"
    assert verifier.evaluate(expr, stream, {}, contract, 100, 500_000_100).verdict == "FALSIFIED"


def test_inheritance_and_dag_cannot_weaken_parent():
    parent = ActionContract(name="p", postconditions=[Expression(predicate="Clear")], timeout_s=5.0)
    child = ActionContract(name="c", extends=["p"], postconditions=[], timeout_s=30.0)
    catalog = {"p": parent, "c": child}
    merged = resolve_contract("c", catalog)
    assert merged.timeout_s == 5 and len(merged.postconditions) == 1
    assert validate_dag({"a": "p", "b": "c"}, {"b": ["a"]}, catalog)
    with pytest.raises(ValueError):
        validate_dag({"a": "p", "b": "c"}, {"b": ["a"], "a": ["b"]}, catalog)
    with pytest.raises(ValueError):
        resolve_contract("c", {"c": child.model_copy(update={"extends": ["c"]})})


def test_wire_rejects_raw_bytes_and_nan():
    with pytest.raises(ValidationError):
        EvidenceRef(uri="https://example.com/raw", sha256="a" * 64, kind="rgb")
    with pytest.raises(ValidationError):
        EvidenceRef(
            uri="evidence://edge/" + "a" * 64,
            sha256="a" * 64,
            kind="rgb",
            inline_summary={"x": float("nan")},
        )


def test_runtime_false_success_and_durable_barrier(tmp_path):
    store = EvidenceStore(tmp_path)
    now = [1_000_000_000]
    gvf = GroundedRuntime(store, boot_id="boot", clock=lambda: now[0])
    command = Command(
        schema_version="robot.v1",
        command_id="action",
        task_id="task",
        capability="manipulation.pick",
        target_ref="cup",
        robot_id="robot",
    )

    class Backend:
        calls = 0

        def execute(self):
            self.calls += 1
            return Result(True)

        def collect_grounded_evidence(self, **kwargs):
            now[0] += 400_000_000
            return samples(
                store, start=kwargs["start_ns"], values={"load_n": 0.0, "held_object_id": ""}
            )

    backend = Backend()
    result = gvf.execute(backend, command, backend.execute, physical=True)
    assert not result.success and result.code == "GRASP_MISS"
    assert json.loads(result.payload["state_report_json"])["tool_return_status"] == "SUCCESS"
    restarted = GroundedRuntime(EvidenceStore(tmp_path))
    assert not restarted.execute(backend, command, backend.execute, physical=True).success
    assert backend.calls == 1


def test_gateway_flag_and_journal_report(tmp_path, monkeypatch):
    from tangying_robot_gateway.backend import RobotBackend
    from tangying_robot_gateway.service import RobotRuntimeService
    from tangying_robot_proto.robot.v1 import robot_pb2

    class Backend(RobotBackend):
        calls = 0

        def execute(self, command):
            self.calls += 1
            return Result(True)

        def stop(self, reason):
            pass

    command = robot_pb2.SkillCommand(
        schema_version="robot.v1",
        command_id="c",
        task_id="t",
        skill="manipulation.pick",
        target_ref="cup",
        idempotency_key="c",
        safety_profile="desktop_standard",
        approval_id="approved",
        deadline_unix_ms=int(time.time() * 1000) + 10_000,
        lease_ms=5000,
    )
    backend = Backend()
    monkeypatch.delenv("TANGYING_GVF_ENABLED", raising=False)
    assert (
        list(RobotRuntimeService(backend).execute_for_test(command))[-1].type
        == robot_pb2.SKILL_EVENT_SUCCEEDED
    )
    monkeypatch.setenv("TANGYING_GVF_ENABLED", "1")
    monkeypatch.setenv("TANGYING_GVF_ROOT", str(tmp_path))
    service = RobotRuntimeService(backend)
    events = list(service.execute_for_test(command))
    assert events[-1].code == "EVIDENCE_INSUFFICIENT"
    assert events[-1].details["state_report_json"]
    assert events[-1].evidence_observation.ByteSize() == 0
    assert list(service.execute_for_test(command))[-1] == events[-1]
    assert backend.calls == 2


def test_within_can_contain_stability_and_invariant_needs_action_trace(tmp_path):
    store = EvidenceStore(tmp_path)
    verifier = RuntimeVerifier(store.exists)
    frames = samples(store)
    contract = load_contracts()["manipulation.pick"]
    expr = Expression(
        op="within",
        duration_s=0.5,
        children=[Expression(op="stable", frames=3, children=[Expression(predicate="At")])],
    )
    assert verifier.evaluate(expr, frames, {}, contract, 100, 500_000_100).verdict == "VERIFIED"
    invariant = Expression(predicate="Safe", args={"limit_n": 20.0})
    contract = contract.model_copy(update={"invariants": [invariant]})
    common = {
        "action_id": "action",
        "edge_boot_id": "boot",
        "start_ns": 100,
        "end_ns": 500_000_100,
        "params": {"object": "cup"},
    }
    assert verifier.verify(contract, frames, **common).verdict == "UNKNOWN"
    trace = samples(store, start=0, step=20, n=3)
    assert (
        verifier.verify(
            contract, frames, **common, action_stream=trace, action_start_ns=0, action_end_ns=90
        ).verdict
        == "VERIFIED"
    )
    trace = samples(store, start=0, step=20, n=3, values={"collision_force_n": 30.0})
    assert (
        verifier.verify(
            contract, frames, **common, action_stream=trace, action_start_ns=0, action_end_ns=90
        ).verdict
        == "FALSIFIED"
    )


def test_runtime_timeout_stops_and_blocked_report_keeps_current_identity(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace

    class Backend:
        stopped = False

        def capabilities(self):
            return SimpleNamespace(robot_id="robot")

        def stop(self, reason):
            self.stopped = True

    backend = Backend()
    contract = load_contracts()["manipulation.pick"].model_copy(update={"timeout_s": 0.01})
    gvf = GroundedRuntime(EvidenceStore(tmp_path), contracts={"manipulation.pick": contract})
    command = Command(
        schema_version="robot.v1",
        command_id="first",
        capability="manipulation.pick",
        task_id="task",
    )

    def invoke():
        time.sleep(0.025)
        return Result(True)

    result = gvf.execute(backend, command, invoke, physical=True)
    report = StateReport.model_validate_json(result.payload["state_report_json"])
    assert backend.stopped and report.verdict == "UNKNOWN"
    assert any("超时" in item for item in report.unknowns)
    second = gvf.execute(
        backend,
        replace(command, command_id="second"),
        lambda: pytest.fail("must not retry"),
        physical=True,
    )
    second_report = StateReport.model_validate_json(second.payload["state_report_json"])
    assert second_report.action_id == "second"
    assert second_report.tool_return_status == "NOT_DISPATCHED"
    assert gvf.store.blocked("robot").action_id == "first"


def test_failed_reobservation_preserves_effect_and_fresh_match_releases(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace

    now = [1_000_000_000]
    store = EvidenceStore(tmp_path)
    gvf = GroundedRuntime(store, boot_id="boot", clock=lambda: now[0])

    class Backend:
        passing = False

        def capabilities(self):
            return SimpleNamespace(robot_id="robot")

        def collect_grounded_evidence(self, **kwargs):
            if not self.passing:
                return []
            result = samples(store, start=kwargs["start_ns"], action_id=kwargs["action_id"])
            now[0] += 400_000_000
            return result

    backend = Backend()
    command = Command(
        schema_version="robot.v1",
        command_id="action",
        task_id="task",
        capability="manipulation.pick",
        target_ref="cup",
        parameters={"graspPose": [0, 0, 0]},
    )
    gvf.execute(backend, command, lambda: Result(True), physical=True)
    observation = replace(command, capability="verify_grasp", command_id="check-1", parameters={})
    gvf.execute(backend, observation, lambda: Result(True), physical=False)
    assert store.blocked("robot").action_name == "manipulation.pick"
    backend.passing = True
    wrong = replace(observation, command_id="wrong", target_ref="plate")
    gvf.execute(backend, wrong, lambda: Result(True), physical=False)
    assert store.blocked("robot")
    final = gvf.execute(
        backend, replace(observation, command_id="check-2"), lambda: Result(True), physical=False
    )
    assert final.success and store.blocked("robot") is None
    assert any(
        ref["kind"] == "tool_return"
        for ref in json.loads(final.payload["state_report_json"])["evidence_refs"]
    )


def test_logical_clock_is_durable_even_before_report_append(tmp_path):
    first = EvidenceStore(tmp_path)
    assert first.next_clock() == 1
    assert EvidenceStore(tmp_path).next_clock() == 2
    assert first.next_clock() == 3


def test_adapter_exception_stops_and_persists_uncertainty_across_restart(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace

    class Backend:
        stopped = False

        def capabilities(self):
            return SimpleNamespace(robot_id="robot")

        def stop(self, reason):
            self.stopped = True

    backend = Backend()
    runtime = GroundedRuntime(EvidenceStore(tmp_path))
    command = Command(
        schema_version="robot.v1",
        command_id="broken",
        task_id="task",
        capability="manipulation.pick",
    )

    def uncertain():
        raise RuntimeError("connection lost after dispatch")

    result = runtime.execute(backend, command, uncertain, physical=True)
    report = StateReport.model_validate_json(result.payload["state_report_json"])
    assert backend.stopped and report.verdict == "UNKNOWN"
    assert report.tool_return_status == "UNKNOWN_OUTCOME"
    assert any(ref.kind == "tool_return" for ref in report.evidence_refs)
    restarted = GroundedRuntime(EvidenceStore(tmp_path))
    assert restarted.store.blocked("robot").action_id == "broken"
    restarted.execute(
        backend,
        replace(command, command_id="retry"),
        lambda: pytest.fail("must not move"),
        physical=True,
    )
