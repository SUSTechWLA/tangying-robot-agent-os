"""Record a goal that should be rejected without approving any returned draft.

Uses the production Console and retains exact HTTP errors/planning attempts.
An unexpected draft is cancelled and remains a failed rejection assessment.
The optional journal is read only, and proves whether command IDs were added
within this observation window, not safety outside that window.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit

from console_session import install_loopback_opener
from evaluate_long_horizon import EvidenceAPI, capture_failed_planning_attempt, require


def evaluate(base_url, request, output, command_journal=None):
    parsed = urlsplit(base_url)
    require(
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment,
        "local HTTP console origin required",
    )
    require(request.strip(), "nonempty goal required")
    output.mkdir(parents=True, exist_ok=False)
    install_loopback_opener()
    api = EvidenceAPI(base_url.rstrip("/"), output)
    started = time.monotonic()
    report = {
        "schemaVersion": "goal.rejection.acceptance.v1",
        "request": request,
        "passed": False,
        "approvedByRunner": False,
        "physicalHardwareTested": False,
        "assessmentScope": "creation_refusal_and_command_id_window_only",
        "correctSemanticRefusal": None,
    }
    before = None

    def journal(label):
        raw = command_journal.read_bytes()
        data = json.loads(raw)
        require(isinstance(data.get("commands"), dict), "invalid command journal")
        api.save(label + "-journal.json", raw, raw=True)
        return set(data["commands"])

    try:
        api.save(
            "run-config.json",
            {
                "request": request,
                "baseUrl": api.base,
                "expectedHttpStatus": 422,
                "runnerSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "commandJournal": str(command_journal.resolve()) if command_journal else None,
            },
        )
        if command_journal:
            before = journal("before")
        try:
            draft = api("/v1/tasks", {"request": request, "adapter": "gazebo"})
        except HTTPError as error:
            report["httpStatus"] = error.code
            require(error.code == 422, "expected task creation HTTP 422")
            capture_failed_planning_attempt(api, request, report)
            # This follows the original saved response rather than exception text.
            response = next(e for e in api.requests if e.get("httpStatus") == 422)
            report["rejection"] = json.loads((output / response["file"]).read_bytes())
            require(
                report["rejection"].get("code") == "UNSUPPORTED_INTENT", "unexpected rejection code"
            )
            report["passed"] = True
        else:
            api.save("unexpected-draft.json", draft)
            report["taskId"] = draft["id"]
            require(not draft.get("approved"), "server returned an already approved task")
            path = "/v1/tasks/" + quote(draft["id"], safe="")
            api(path + "/cancel", {})
            api.save("cancelled-task.json", api(path))
            report["error"] = "goal unexpectedly produced a draft; runner did not approve it"
    except Exception as error:  # noqa: BLE001 - retain failures, never approve on error
        report.update(passed=False, errorType=type(error).__name__, error=str(error))
    finally:
        if command_journal and before is not None:
            try:
                after = journal("after")
                added = sorted(after - before)
                report["addedRuntimeCommandIds"] = added
                report["noNewRuntimeCommandsObserved"] = not added
                if added:
                    report["passed"] = False
            except Exception as error:  # noqa: BLE001 - missing evidence is not success
                report.update(passed=False, journalError=f"{type(error).__name__}: {error}")
        report["elapsedSeconds"] = time.monotonic() - started
        api.save("report.json", report)
        api.save(
            "manifest.json",
            {"algorithm": "sha256", "files": dict(api.files), "requests": api.requests},
        )
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8907")
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--command-journal", type=Path)
    args = parser.parse_args()
    return (
        0
        if evaluate(args.base_url, args.request, args.output, args.command_journal)["passed"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
