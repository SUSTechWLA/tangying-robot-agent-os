"""Evidence/stop semantics of the exploration harness; no simulator or motion."""

import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import grpc
import pytest
from google.protobuf.json_format import MessageToDict

from scripts import validate_exploration_first_scan as suite


class LostStartResponse(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.UNAVAILABLE

    def details(self):
        return "fixture dropped the response after supervised start"


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class FakeRuntime:
    def __init__(self, mode, clock, output):
        self.mode, self.clock, self.output = mode, clock, output
        self.started = False
        self.cancelled = False
        self.requests = []

    def GetRuntimeInfo(self, _request, timeout):
        assert (self.output / "manifest.json").is_file(), "manifest must precede first RPC"
        assert (self.output / "preregistration.json").is_file()
        return suite.pb.RuntimeInfo(robot_id="gazebo-fixture", adapter="gazebo")

    def ListServices(self, _request, timeout):
        result = suite.pb.ServiceCatalog(robot_id="gazebo-fixture")
        for name in ("mapping.build", "mapping.status", "mapping.cancel", "mapping.inventory", "navigation.status"):
            result.services.add(name=name, available=True)
        return result

    def CallService(self, request, timeout):
        self.requests.append(MessageToDict(request))
        if request.name == "mapping.inventory":
            data = {"count": 1 if self.mode == "existing_map" else 0, "availableMaps": []}
        elif request.name == "navigation.status":
            data = {"mode": "mapping", "mapId": "scan-fixture" if self.started else "",
                    "mapRevision": "revision" if self.started else "",
                    "ready": self.mode != "not_ready", "localizationState": "localized"}
        elif request.name == "mapping.build":
            assert request.operation_lease_ms == 30_000
            assert MessageToDict(request.parameters) == {
                "mode": "explore", "name": "firstscan-test", "maxTravelM": 8, "maxLegs": 1,
            }
            self.started = True
            if self.mode == "lost_start":
                raise LostStartResponse()
            data = self.status()
        elif request.name == "mapping.cancel":
            self.cancelled = True
            data = self.status()
        else:
            assert request.name == "mapping.status"
            data = self.status()
        response = suite.pb.ServiceResponse(ok=True, code="OK")
        response.result.update(data)
        return response

    def status(self):
        if not self.started:
            return {"state": "idle"}
        if self.cancelled or (self.mode == "lost_start" and self.clock.value >= 30):
            return {"state": "cancelled", "operationId": "operation-fixture"}
        if self.mode in {"timeout", "lost_start"}:
            return {"state": "exploring", "operationId": "operation-fixture", "trajectory": [[1, 2, 3]]}
        return {
            "state": "completed", "operationId": "operation-fixture", "mapId": "scan-fixture",
            "activeMap": {"mapId": "scan-fixture", "mapRevision": "revision", "calibrationRevision": "calibration"},
            "operationTravelledM": 2.0, "trajectory": [[1, 2, 3]], "preview": {"points": [1]},
            "exploration": {"leg": 1, "stopReason": "travel_budget", "initialScanAttempted": True,
                            "initialScanStatus": "returned"},
        }


@pytest.fixture
def setup_run(tmp_path, monkeypatch):
    def prepare(mode="success"):
        output = tmp_path / mode
        clock = Clock()
        runtime = FakeRuntime(mode, clock, output)
        monkeypatch.setattr(suite, "time", SimpleNamespace(
            monotonic=clock.monotonic, sleep=clock.sleep, time_ns=time.time_ns))
        monkeypatch.setattr(suite.grpc, "insecure_channel", lambda _: object())
        monkeypatch.setattr(suite.rpc, "RobotRuntimeStub", lambda _: runtime)
        monkeypatch.setattr(suite, "source_snapshot", lambda _: {"fixture.py": "a" * 64})
        monkeypatch.setattr(suite, "git", lambda *args: b"fixture-head\n" if args[0] == "rev-parse" else b"")

        def archive(_root, target, map_id):
            assert map_id == "scan-fixture"
            (target / "map-package").mkdir()
            suite.write_json(target / "map-package" / "manifest.json", {"mapId": map_id})
            return {"robotId": "gazebo-fixture", "calibrationRevision": "calibration"}

        monkeypatch.setattr(suite, "archive_map", archive)
        args = SimpleNamespace(output=output, runtime="fixture.invalid:1", name="firstscan-test",
                               max_travel_m=8, max_legs=1, source_root=Path("/fixture-source"),
                               map_root=tmp_path, environment_json=None,
                               timeout=0.1 if mode == "timeout" else 900, poll_s=5)
        return args, runtime, clock

    return prepare


def read_report(output):
    return json.loads((output / "report.json").read_text())


def assert_integrity(output):
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["state"] == "finalized"
    for name, expected in manifest["files"].items():
        data = (output / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected["sha256"]
        assert len(data) == expected["bytes"]


def test_success_retains_trajectory_without_claiming_environment_coverage(setup_run):
    args, runtime, _ = setup_run()
    assert suite.evaluate(args) == 0
    report = read_report(args.output)
    assert report["passed"] and report["mapArtifactVerification"]
    assert report["coverageGroundTruth"] == "not_measured"
    assert not report["wholeEnvironmentCoverage"] and not report["naturalLanguageHostTest"]
    progress = (args.output / "progress.jsonl").read_text()
    assert '"trajectory"' in progress and '"preview"' not in progress
    assert [request["name"] for request in runtime.requests].count("mapping.build") == 1
    assert_integrity(args.output)


def test_existing_map_is_rejected_before_any_motion(setup_run):
    args, runtime, _ = setup_run("existing_map")
    assert suite.evaluate(args) == 1
    assert "empty registered-map inventory" in read_report(args.output)["error"]
    assert not runtime.started and not runtime.cancelled
    assert_integrity(args.output)


def test_timeout_cancels_once_and_confirms_same_operation_stopped(setup_run):
    args, runtime, _ = setup_run("timeout")
    assert suite.evaluate(args) == 1
    report = read_report(args.output)
    assert report["state"] == "cancelled" and report["stopConfirmed"]
    assert "TimeoutError" in report["error"] and not report["passed"]
    assert [item["name"] for item in runtime.requests].count("mapping.cancel") == 1
    assert_integrity(args.output)


def test_output_cannot_be_overwritten(setup_run):
    args, runtime, _ = setup_run()
    assert suite.evaluate(args) == 0
    previous = (args.output / "manifest.json").read_bytes()
    requests_before = len(runtime.requests)
    with pytest.raises(FileExistsError):
        suite.evaluate(args)
    assert (args.output / "manifest.json").read_bytes() == previous
    assert len(runtime.requests) == requests_before


def test_unknown_start_waits_for_lease_without_cancel_or_renewal(setup_run):
    args, runtime, clock = setup_run("lost_start")
    assert suite.evaluate(args) == 1
    report = read_report(args.output)
    assert report["unknownStartOutcome"] and report["stopConfirmed"]
    assert report["state"] == "cancelled" and clock.value >= 30
    assert not runtime.cancelled  # the fixture's provider lease, not a client cancel
    start_at = next(i for i, request in enumerate(runtime.requests) if request["name"] == "mapping.build")
    later = runtime.requests[start_at + 1:]
    assert later and all(item["name"] == "mapping.status" for item in later)
    assert all("operationLeaseMs" not in item for item in later)
    assert_integrity(args.output)


def test_loaded_but_unready_navigation_does_not_pass(setup_run):
    args, _, _ = setup_run("not_ready")
    assert suite.evaluate(args) == 1
    assert "not ready" in read_report(args.output)["error"]
    assert_integrity(args.output)


def test_changed_source_invalidates_success(setup_run, monkeypatch):
    args, _, _ = setup_run()
    source_versions = iter(({"file": "old"}, {"file": "new"}))
    monkeypatch.setattr(suite, "source_snapshot", lambda _: next(source_versions))
    assert suite.evaluate(args) == 1
    report = read_report(args.output)
    assert not report["passed"] and report["sourceChangedDuringRun"]
    assert_integrity(args.output)
