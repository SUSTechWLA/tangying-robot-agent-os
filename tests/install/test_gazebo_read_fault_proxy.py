"""Local gRPC fake-runtime tests; these do NOT certify actual Gazebo physics."""

import json
import threading
from concurrent import futures
from contextlib import contextmanager
from types import SimpleNamespace

import grpc
import pytest

from scripts.gazebo_read_fault_proxy import ReadFaultProxy, loopback_endpoint, pb, pbg, start_proxy


class FakeGazeboRuntime(pbg.RobotRuntimeServicer):
    """Explicit fake reports Gazebo solely to exercise the proxy's wire gate."""

    def __init__(self, adapter="gazebo", mutates_world=False, service="navigation.status"):
        self.adapter = adapter
        self.mutates_world = mutates_world
        self.service = service
        self.calls = []
        self.lock = threading.Lock()
        self.blocked = threading.Event()
        self.cancelled = threading.Event()

    def _record(self, method, request, context):
        with self.lock:
            self.calls.append(
                (method, request, dict(context.invocation_metadata()), context.time_remaining())
            )
        context.send_initial_metadata((("x-fake-initial", method),))
        context.set_trailing_metadata((("x-fake-trailer", method),))

    def GetRuntimeInfo(self, request, context):
        self._record("GetRuntimeInfo", request, context)
        return pb.RuntimeInfo(adapter=self.adapter, robot_id="fake-gazebo-for-test")

    def ListServices(self, request, context):
        self._record("ListServices", request, context)
        return pb.ServiceCatalog(
            services=[
                pb.ServiceDefinition(
                    name=self.service,
                    mutates_world=self.mutates_world,
                    available=True,
                )
            ]
        )

    def CallService(self, request, context):
        self._record("CallService", request, context)
        if request.name == "upstream.error":
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "fake upstream error")
        if request.name == "upstream.block":
            context.add_callback(self.cancelled.set)
            self.blocked.set()
            self.cancelled.wait(timeout=5)
        return pb.ServiceResponse(ok=True, code="FAKE_OK", message=request.request_id)

    def Observe(self, request, context):
        self._record("Observe", request, context)
        if request.task_id == "large-rgbd":
            yield pb.Observation(observation_id="large", compressed_image=b"x" * (5 * 1024 * 1024))
            return
        if request.task_id == "block":
            context.add_callback(self.cancelled.set)
            yield pb.Observation(observation_id="before-cancel")
            self.blocked.set()
            self.cancelled.wait(timeout=5)
            return
        for i in range(3):
            yield pb.Observation(observation_id=f"fake-observation-{i}")
        if request.task_id == "error":
            context.abort(grpc.StatusCode.ABORTED, "fake stream error")

    def ExecuteSkill(self, request, context):
        self._record("ExecuteSkill", request, context)
        for sequence, event_type in enumerate((pb.SKILL_EVENT_ACCEPTED, pb.SKILL_EVENT_SUCCEEDED)):
            yield pb.SkillEvent(command_id=request.command_id, sequence=sequence, type=event_type)

    def Cancel(self, request, context):
        self._record("Cancel", request, context)
        return pb.CancelResult(accepted=True, state=request.command_id)

    def EmergencyStop(self, request, context):
        self._record("EmergencyStop", request, context)
        return pb.EStopResult(latched=True, stopped_unix_ms=12345)


@contextmanager
def fake_runtime(**kwargs):
    runtime = FakeGazeboRuntime(**kwargs)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    pbg.add_RobotRuntimeServicer_to_server(runtime, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        yield runtime, f"127.0.0.1:{port}"
    finally:
        runtime.cancelled.set()
        server.stop(grace=0).wait()


@contextmanager
def proxy_pair(tmp_path, **kwargs):
    with fake_runtime() as (runtime, address):
        proxy = start_proxy(
            runtime=address, listen="127.0.0.1:0", output=tmp_path / "faults.jsonl", **kwargs
        )
        channel = grpc.insecure_channel(
            proxy.address, options=(("grpc.max_receive_message_length", 16 * 1024 * 1024),)
        )
        try:
            yield runtime, pbg.RobotRuntimeStub(channel)
        finally:
            channel.close()
            proxy.stop()


def request(request_id="task-recovery-1", name="navigation.status"):
    return pb.ServiceRequest(name=name, request_id=request_id)


def test_armed_one_shot_skips_planning_verification_and_writes(tmp_path):
    marker = tmp_path / "armed"
    with proxy_pair(tmp_path, arm_file=marker) as (runtime, stub):
        assert stub.CallService(request("task-baseline"), timeout=2).ok
        marker.touch()
        for request_id in ("planning-1", "verify-task-1", "", "taskish-1"):
            assert stub.CallService(request(request_id), timeout=2).ok
        assert stub.CallService(request(name="navigation.navigate"), timeout=2).ok
        with pytest.raises(grpc.RpcError) as failure:
            stub.CallService(
                request(), timeout=2, metadata=(("authorization", "secret-must-not-be-logged"),)
            )
        assert failure.value.code() == grpc.StatusCode.UNAVAILABLE
        assert stub.CallService(request("task-retry"), timeout=2).ok
        upstream_ids = [call[1].request_id for call in runtime.calls if call[0] == "CallService"]
        assert upstream_ids.count("task-recovery-1") == 1  # The unrelated write only.
        assert len(upstream_ids) == 7
    contents = (tmp_path / "faults.jsonl").read_text()
    records = [json.loads(line) for line in contents.splitlines()]
    faults = [record for record in records if record["event"] == "fault_injected"]
    assert len(faults) == 1
    assert faults[0]["request_id"] == "task-recovery-1"
    assert faults[0]["injection_number"] == 1
    assert faults[0]["forwarded"] is False
    assert faults[0]["time_utc"] and faults[0]["monotonic_ns"] > 0
    assert "secret-must-not-be-logged" not in contents
    assert "authorization" not in contents


def test_concurrent_calls_inject_exact_fail_count_and_append_log(tmp_path):
    output = tmp_path / "faults.jsonl"
    output.write_text('{"event":"earlier-run"}\n')
    with proxy_pair(tmp_path, fail_count=3) as (runtime, stub):

        def call(index):
            try:
                return stub.CallService(request(f"task-{index}"), timeout=5).code
            except grpc.RpcError as error:
                return error.code()

        with futures.ThreadPoolExecutor(max_workers=12) as workers:
            outcomes = list(workers.map(call, range(12)))
        assert outcomes.count(grpc.StatusCode.UNAVAILABLE) == 3
        assert outcomes.count("FAKE_OK") == 9
        assert sum(call[0] == "CallService" for call in runtime.calls) == 9
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert records[0]["event"] == "earlier-run"
    assert [r["injection_number"] for r in records if r["event"] == "fault_injected"] == [1, 2, 3]


def test_all_seven_methods_preserve_payload_streams_and_metadata(tmp_path):
    with proxy_pair(tmp_path) as (runtime, stub):
        opts = {"timeout": 4, "metadata": (("x-client-test", "forward-this"),)}
        info, call = stub.GetRuntimeInfo.with_call(pb.GetRuntimeInfoRequest(), **opts)
        assert info.robot_id == "fake-gazebo-for-test"
        assert ("x-fake-initial", "GetRuntimeInfo") in call.initial_metadata()
        assert ("x-fake-trailer", "GetRuntimeInfo") in call.trailing_metadata()
        assert (
            stub.ListServices(pb.GetRuntimeInfoRequest(), **opts).services[0].name
            == "navigation.status"
        )
        assert stub.CallService(request("planning"), **opts).message == "planning"
        assert (
            stub.Cancel(pb.CancelRequest(command_id="command-123"), **opts).state == "command-123"
        )
        assert (
            stub.EmergencyStop(pb.EStopRequest(reason="operator"), **opts).stopped_unix_ms == 12345
        )
        observations = stub.Observe(pb.ObserveRequest(task_id="observe-test"), **opts)
        assert [o.observation_id for o in observations] == [
            f"fake-observation-{i}" for i in range(3)
        ]
        assert ("x-fake-initial", "Observe") in observations.initial_metadata()
        assert ("x-fake-trailer", "Observe") in observations.trailing_metadata()
        command = pb.SkillCommand(command_id="command-456", task_id="task-123", lease_ms=321)
        events = stub.ExecuteSkill(command, **opts)
        assert [event.type for event in events] == [
            pb.SKILL_EVENT_ACCEPTED,
            pb.SKILL_EVENT_SUCCEEDED,
        ]
        assert ("x-fake-trailer", "ExecuteSkill") in events.trailing_metadata()
        forwarded = runtime.calls[2:]  # Initial two calls are the startup gate.
        assert len(forwarded) == 7
        assert all(call[2]["x-client-test"] == "forward-this" for call in forwarded)
        assert all(0 < call[3] < 4.1 for call in forwarded)
        assert next(call[1] for call in forwarded if call[0] == "ExecuteSkill") == command


@pytest.mark.parametrize("streaming", [False, True])
def test_upstream_errors_and_trailers_are_preserved(tmp_path, streaming):
    with proxy_pair(tmp_path) as (_, stub):
        with pytest.raises(grpc.RpcError) as failure:
            if streaming:
                list(stub.Observe(pb.ObserveRequest(task_id="error"), timeout=2))
            else:
                stub.CallService(request(name="upstream.error"), timeout=2)
        method = "Observe" if streaming else "CallService"
        assert failure.value.code() == (
            grpc.StatusCode.ABORTED if streaming else grpc.StatusCode.FAILED_PRECONDITION
        )
        assert failure.value.details() == (
            "fake stream error" if streaming else "fake upstream error"
        )
        assert ("x-fake-trailer", method) in failure.value.trailing_metadata()


@pytest.mark.parametrize("streaming", [False, True])
def test_downstream_cancel_reaches_upstream(tmp_path, streaming):
    with proxy_pair(tmp_path) as (runtime, stub):
        if streaming:
            call = stub.Observe(pb.ObserveRequest(task_id="block"), timeout=5)
            assert next(call).observation_id == "before-cancel"
        else:
            call = stub.CallService.future(request(name="upstream.block"), timeout=5)
        assert runtime.blocked.wait(timeout=2)
        assert call.cancel()
        assert runtime.cancelled.wait(timeout=2)


def test_deadline_expires_upstream_and_downstream(tmp_path):
    with proxy_pair(tmp_path) as (runtime, stub):
        with pytest.raises(grpc.RpcError) as failure:
            stub.CallService(request(name="upstream.block"), timeout=0.2)
        assert failure.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
        assert runtime.cancelled.wait(timeout=2)
        actual_deadline = next(call[3] for call in runtime.calls if call[0] == "CallService")
        assert 0 < actual_deadline < 0.25


def test_large_observation_and_absent_deadline_are_forwarded(tmp_path):
    with proxy_pair(tmp_path) as (_, stub):
        assert (
            stub.GetRuntimeInfo.future(pb.GetRuntimeInfoRequest()).result(timeout=2).adapter
            == "gazebo"
        )
        observations = list(stub.Observe(pb.ObserveRequest(task_id="large-rgbd"), timeout=3))
        assert len(observations) == 1
        assert observations[0].compressed_image == b"x" * (5 * 1024 * 1024)


@pytest.mark.parametrize("remaining,expected", [
    (None, None), (float("inf"), None), (9.223372035063561e18, None),
    (float(2**63-1), None), (99_999_999 * 3600 + 1, None),
    (0., 0.), (.25, .25), (65., 65.), (3600., 3600.),
    (99_999_999 * 3600, 99_999_999 * 3600),
])
def test_deadline_options_distinguish_core_infinity_from_finite_wire_timeout(remaining, expected):
    metadata = (("x-client-test", "keep-metadata"),)
    context = SimpleNamespace(time_remaining=lambda: remaining, invocation_metadata=lambda: metadata)
    assert ReadFaultProxy._options(context) == {"timeout": expected, "metadata": metadata}


def test_unary_and_streaming_without_deadline_forward_none_over_real_grpc(tmp_path, monkeypatch):
    forwarded = []
    options = ReadFaultProxy._options

    def capture_options(context):
        values = options(context)
        forwarded.append(values["timeout"])
        return values

    monkeypatch.setattr(ReadFaultProxy, "_options", staticmethod(capture_options))
    with proxy_pair(tmp_path) as (runtime, stub):
        assert stub.GetRuntimeInfo.future(pb.GetRuntimeInfoRequest()).result(timeout=2).adapter == "gazebo"
        stream = stub.Observe(pb.ObserveRequest(task_id="no-deadline"))
        with futures.ThreadPoolExecutor(max_workers=1) as workers:
            try:
                observations = workers.submit(list, stream).result(timeout=2)
            finally:
                stream.cancel()
        assert len(observations) == 3
        assert forwarded == [None, None]
        assert all(call[3] is None or call[3] > 99_999_999 * 3600 for call in runtime.calls[2:])


@pytest.mark.parametrize(
    "runtime_options,match",
    [
        ({"adapter": "hardware"}, "adapter"),
        ({"mutates_world": True}, "mutating"),
        ({"service": "unknown"}, "exactly once"),
    ],
)
def test_startup_refuses_non_gazebo_missing_or_mutating_service(tmp_path, runtime_options, match):
    with fake_runtime(**runtime_options) as (_, address), pytest.raises(ValueError, match=match):
        start_proxy(runtime=address, listen="127.0.0.1:0", output=tmp_path / "faults.jsonl")
    assert not (tmp_path / "faults.jsonl").exists()


@pytest.mark.parametrize(
    "endpoint",
    [
        "0.0.0.0:1",
        "192.168.1.1:1",
        "[::]:1",
        "example.com:1",
        "dns:///127.0.0.1:1",
        "127.0.0.1:0",
        "127.0.0.1:65536",
    ],
)
def test_refuse_nonloopback_or_invalid_endpoints(endpoint):
    with pytest.raises(ValueError, match="loopback"):
        loopback_endpoint(endpoint)


def test_loopback_ipv6_and_localhost_are_accepted():
    assert loopback_endpoint("localhost:1234") == "127.0.0.1:1234"
    assert loopback_endpoint("[::1]:1234") == "[::1]:1234"


@pytest.mark.parametrize("field", ["runtime", "listen"])
def test_startup_validates_both_endpoints_before_connecting(tmp_path, field):
    options = {"runtime": "127.0.0.1:50161", "listen": "127.0.0.1:50162"}
    options[field] = "0.0.0.0:1234"
    with pytest.raises(ValueError, match="loopback"):
        start_proxy(**options, output=tmp_path / "faults.jsonl")
