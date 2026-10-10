"""Failed GOAL attempts remain failed while retaining exact read-only evidence."""

import hashlib
import io
import json
from urllib.error import HTTPError
from urllib.parse import quote

import pytest

from scripts import evaluate_long_horizon as suite

GOAL = "建图，然后拿杯子"
ATTEMPT_ID = "planning-attempt-原始/id"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def retained_attempt():
    scope = {"task_id": "", "robot_id": "gazebo-home", "plan_revision": 0}
    text = json.dumps({"scope": scope, "goal": GOAL}, ensure_ascii=False, separators=(",", ":"))
    archive = '[ {"sourceStamp":9007199254740993,"text":"杯子"} ]'
    model = json.dumps({"messages": [{"role": "user", "content": text}]}, ensure_ascii=False)
    context = {"scope": scope, "stage": "planning", "text": text, "sha256": sha(text),
               "artifacts": [{"scope": scope, "data_json": archive, "sha256": sha(archive)}],
               "model_request_json": model, "model_request_sha256": sha(model)}
    trace = json.dumps([{"round": 1, "tool": "navigation.status", "verdict": "SATISFIED", "context": context}], ensure_ascii=False)
    return {"schemaVersion": "planning.attempt.v1", "id": ATTEMPT_ID,
            "createdAt": "2026-10-09T20:30:00Z", "request": GOAL, "error": "planning exceeded decision budget",
            "source": "llm", "scope": scope, "robotId": "gazebo-home", "catalogRevision": "catalog",
            "originalSourceTrace": trace, "sourceTraceSHA256": sha(trace), "traceAvailable": True}


def install_http(monkeypatch, *, attempt=None, failure=None, get_error=None):
    calls = []
    attempt = retained_attempt() if attempt is None else attempt
    failure = {"code": "UNSUPPORTED_INTENT", "message": "planning exceeded decision budget", "planningAttemptId": ATTEMPT_ID} if failure is None else failure
    failure_raw = json.dumps(failure, ensure_ascii=False).encode()
    attempt_raw = json.dumps(attempt, ensure_ascii=False, indent=2).encode()+b"\n"
    monkeypatch.setattr(suite, "resolve_token", lambda **_: "private-session-never-print")
    monkeypatch.setattr(suite, "install_loopback_opener", lambda: None)

    def fetch(request, timeout):
        method, path = request.get_method(), request.full_url.removeprefix("http://127.0.0.1:8897")
        calls.append((method, path))
        if (method, path) == ("GET", "/v1/robot/services"):
            # EvidenceAPI.last_raw retains this stale successful response when
            # POST raises HTTPError. It must never supply the audit identity.
            return io.BytesIO(b'{"services":[],"planningAttemptId":"wrong-stale-id"}')
        if (method, path) == ("POST", "/v1/tasks"):
            raise HTTPError(request.full_url, 422, "Unprocessable Entity", {}, io.BytesIO(failure_raw))
        assert (method, path) == ("GET", "/v1/planning-attempts/"+quote(ATTEMPT_ID, safe=""))
        if get_error:
            raise HTTPError(request.full_url, get_error, "Unavailable", {}, io.BytesIO(b'{"error":"unavailable"}'))
        return io.BytesIO(attempt_raw)

    monkeypatch.setattr(suite, "urlopen", fetch)
    return calls, attempt_raw, failure_raw


def run(tmp_path):
    return suite.evaluate("http://127.0.0.1:8897", GOAL, tmp_path/"run", settle_seconds=0)


def test_422_captures_exact_attempt_without_retry_or_task_fallback(tmp_path, monkeypatch):
    calls, attempt_raw, failure_raw = install_http(monkeypatch)
    report = run(tmp_path)
    assert not report["passed"] and report["errorType"] == "HTTPError" and "422" in report["error"]
    assert report["planningAttemptId"] == ATTEMPT_ID
    assert report["planningModelRounds"] == report["planningDecisionRounds"] == 1
    assert report["planningAttemptTraceVerified"] and report["planningAttemptTraceAvailable"]
    assert "taskId" not in report and "state" not in report
    assert calls == [("GET", "/v1/robot/services"), ("POST", "/v1/tasks"),
                     ("GET", "/v1/planning-attempts/"+quote(ATTEMPT_ID, safe=""))]
    out = tmp_path/"run"
    assert (out/"response-00002.json").read_bytes() == failure_raw
    assert (out/"planning-attempt.json").read_bytes() == (out/"response-00003.json").read_bytes() == attempt_raw
    assert not (out/"final-task.json").exists() and not (out/"draft-plan.json").exists()
    manifest = json.loads((out/"manifest.json").read_bytes())
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((out/name).read_bytes()).hexdigest() == expected
    with pytest.raises(FileExistsError):
        run(tmp_path)


@pytest.mark.parametrize("failure", [{"auditUnavailable": True}, {"code": "UNSUPPORTED_INTENT"}])
def test_unavailable_or_legacy_audit_does_not_guess_endpoint(tmp_path, monkeypatch, failure):
    calls, _, _ = install_http(monkeypatch, failure=failure)
    report = run(tmp_path)
    assert not report["passed"] and report["planningAttemptAuditUnavailable"]
    assert "planningAttemptId" not in report and len(calls) == 2


def test_failed_attempt_lookup_cannot_replace_original_http_error(tmp_path, monkeypatch):
    calls, _, _ = install_http(monkeypatch, get_error=503)
    report = run(tmp_path)
    assert report["errorType"] == "HTTPError" and "422" in report["error"]
    assert "503" in report["planningAttemptCaptureError"] and not report["passed"]
    assert len(calls) == 3 and not (tmp_path/"run/planning-attempt.json").exists()
    assert (tmp_path/"run/response-00003.json").exists()


@pytest.mark.parametrize("corruption", ["id", "request", "task", "revision", "trace_sha", "context_sha", "model_sha", "model_scope", "archive_sha", "model_content"])
def test_bad_attempt_is_retained_but_never_credited(tmp_path, monkeypatch, corruption):
    attempt = retained_attempt()
    trace = json.loads(attempt["originalSourceTrace"])
    context = trace[0]["context"]
    if corruption == "id":
        attempt["id"] = "different-attempt"
    elif corruption == "request":
        attempt["request"] = "different goal"
    elif corruption == "task":
        attempt["calls"] = [{"tool": "mapping.build"}]
    elif corruption == "revision":
        attempt["scope"]["plan_revision"] = 1
    elif corruption == "trace_sha":
        attempt["sourceTraceSHA256"] = "0"*64
    elif corruption == "context_sha":
        context["sha256"] = "0"*64
    elif corruption == "model_sha":
        context["model_request_sha256"] = "0"*64
    elif corruption == "model_scope":
        context["scope"]["task_id"] = "task-foreign"
    elif corruption == "archive_sha":
        context["artifacts"][0]["data_json"] += " "
    else:
        context["model_request_json"] = '{"messages":[{"role":"user","content":"different context"}]}'
        context["model_request_sha256"] = sha(context["model_request_json"])
    if corruption in {"context_sha", "model_sha", "model_scope", "archive_sha", "model_content"}:
        attempt["originalSourceTrace"] = json.dumps(trace, ensure_ascii=False)
        attempt["sourceTraceSHA256"] = sha(attempt["originalSourceTrace"])
    _, raw, _ = install_http(monkeypatch, attempt=attempt)
    report = run(tmp_path)
    assert not report["passed"] and "422" in report["error"]
    assert "planningAttemptCaptureError" in report and "planningAttemptTraceVerified" not in report
    assert (tmp_path/"run/planning-attempt.json").read_bytes() == raw


def test_empty_durable_trace_is_explicitly_unavailable_not_fake_model_round(tmp_path, monkeypatch):
    attempt = retained_attempt()
    attempt.update(originalSourceTrace="[]", sourceTraceSHA256=sha("[]"), traceAvailable=False)
    install_http(monkeypatch, attempt=attempt)
    report = run(tmp_path)
    assert report["planningModelRounds"] == report["planningDecisionRounds"] == 0
    assert not report["planningAttemptTraceAvailable"] and report["planningAttemptTraceVerified"]
    assert not report["passed"]


@pytest.mark.parametrize("change", ["route", "method", "status", "file", "bytes"])
def test_capture_only_trusts_the_just_saved_local_post_task_422(tmp_path, monkeypatch, change):
    monkeypatch.setattr(suite, "resolve_token", lambda **_: "private")
    api = suite.EvidenceAPI("http://127.0.0.1:8897", tmp_path)
    api.save("failure.json", {"planningAttemptId": ATTEMPT_ID})
    api.requests = [{"method": "POST", "path": "/v1/tasks", "httpStatus": 422, "file": "failure.json"}]
    if change == "bytes":
        (tmp_path/"failure.json").write_text('{}')
    else:
        key, value = {"route": ("path", "/v1/tasks/other/approve"), "method": ("method", "GET"),
                      "status": ("httpStatus", 500), "file": ("file", "../outside.json")}[change]
        api.requests[-1][key] = value
    monkeypatch.setattr(suite, "urlopen", lambda *_args, **_kwargs: pytest.fail("must not fetch an unbound attempt"))
    with pytest.raises(AssertionError):
        suite.capture_failed_planning_attempt(api, GOAL, {})
