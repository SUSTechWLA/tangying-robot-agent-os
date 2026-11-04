"""Mapping evidence collection must not hide failures or cancel a newer scan."""
import importlib.util
import json
from pathlib import Path

import pytest


def load_script():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("build_sim_map", root / "scripts/build_sim_map.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("current, expected, cancels", [
    ({"operationId": "ours", "state": "moving"}, "requested", True),
    ({"operationId": "ours", "state": "failed"}, "not_attempted_terminal", False),
    ({"operationId": "ours", "state": "completed"}, "not_attempted_terminal", False),
    ({"operationId": "other", "state": "moving"}, "not_attempted_operation_changed", False),
])
def test_failure_records_original_and_cancels_only_active_owned_operation(tmp_path, current, expected, cancels):
    module = load_script()
    calls = []

    def service(name, parameters=None):
        calls.append((name, parameters))
        return current if name == "mapping.status" else {}

    error = RuntimeError("original mapping failure")
    module.record_failure(tmp_path, {"state": "moving", "preview": {"large": True}}, error, service, "ours")
    saved = json.loads((tmp_path / "failure.json").read_text())
    assert saved["error"] == str(error)
    assert saved["cancelOutcome"] == expected
    assert "preview" not in saved["lastStatus"]
    assert (len(calls) == 2) == cancels
    if cancels:
        assert calls[-1] == ("mapping.cancel", {"expectedOperationId": "ours"})


def test_failure_preserves_cancel_error_and_does_not_guess_an_identity(tmp_path):
    module = load_script()

    def unavailable(*_):
        raise ValueError("status unavailable")

    module.record_failure(tmp_path, {}, TimeoutError("original timeout"), unavailable, "ours")
    saved = json.loads((tmp_path / "failure.json").read_text())
    assert saved["error"] == "original timeout"
    assert saved["cancelOutcome"] == "status_or_cancel_failed"
    assert saved["cancelError"] == "status unavailable"
    module.record_failure(tmp_path, {}, TimeoutError("start uncertain"), unavailable, "")
    saved = json.loads((tmp_path / "failure.json").read_text())
    assert saved["cancelOutcome"] == "not_attempted_no_operation_identity"


def test_real_runner_keeps_terminal_mapping_error_without_cancelling(tmp_path, monkeypatch):
    module = load_script()
    calls = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(self.body).encode()

    def urlopen(request, timeout):
        assert timeout == 20
        if request.data is None:
            return Response({"services": [{"name": "mapping.start", "available": True}]})
        body = json.loads(request.data)
        calls.append(body["name"])
        if body["name"] == "mapping.start":
            result = {"sessionId": "session1", "operationId": "operation1", "state": "moving"}
        elif body["name"] == "mapping.status":
            result = {"sessionId": "session1", "operationId": "operation1", "state": "failed",
                      "message": "sensor failure", "frameCount": 0, "travelledM": 0}
        else:
            raise AssertionError("terminal operation must not be cancelled")
        return Response({"ok": True, "result": result})

    monkeypatch.setattr(module, "urlopen", urlopen)
    monkeypatch.setattr(module, "resolve_token", lambda **_: "fixture-token")
    monkeypatch.setattr(module, "session_headers", lambda _, headers: headers)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    out = tmp_path / "trial"
    with pytest.raises(RuntimeError, match="sensor failure"):
        module.run("http://127.0.0.1:8898", out, mode="explore")
    saved = json.loads((out / "failure.json").read_text())
    assert saved["error"] == "sensor failure"
    assert saved["statusAtFailure"]["state"] == "failed"
    assert saved["cancelOutcome"] == "not_attempted_terminal"
    assert calls == ["mapping.start", "mapping.status", "mapping.status"]
