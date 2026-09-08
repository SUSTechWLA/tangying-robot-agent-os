"""Run an isolated, camera-only two-goal task across a Local Agent restart.

Uses simulation only. Never connects to an existing robot or deletes its state.
The MuJoCo process remains alive while the Local Agent and its SQLite reopen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"FAILED", "CANCELLED", "RECOVERABLE_FAILURE", "SAFETY_STOPPED", "SUCCEEDED"}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError("acceptance condition timed out")


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run(output: Path, binary: Path, scenario: str = "pause-restart"):
    output.mkdir(parents=True, exist_ok=False)
    robot_port, agent_port = free_port(), free_port()
    while agent_port == robot_port:
        agent_port = free_port()
    base = f"http://127.0.0.1:{agent_port}"
    agent = robot = None
    logs = []

    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def api(path, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode()
        req = request.Request(base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with request.urlopen(req, timeout=15) as response:
                return json.load(response)
        except error.HTTPError as exc:
            raise AssertionError(f"{method} {path}: {exc.code} {exc.read().decode()}") from exc

    def launch(argv, name):
        log = (output / name).open("wb")
        logs.append(log)
        return subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)

    def start_agent(name):
        process = launch(
            [
                str(binary),
                "--listen",
                f"127.0.0.1:{agent_port}",
                "--robot",
                f"127.0.0.1:{robot_port}",
                "--dev-insecure",
                "--robot-safety-profile",
                "simulation",
                "--data-dir",
                str(output / "local-agent"),
            ],
            name,
        )

        def ready():
            if process.poll() is not None:
                raise AssertionError(f"Local Agent exited; inspect {name}")
            try:
                return api("/v1/telemetry?adapter=mujoco&limit=1").get("hasLatest")
            except (OSError, AssertionError):
                return False

        wait_for(ready, 25)
        return process

    def activities(task, tool, status):
        return [
            e
            for e in task.get("events", [])
            if e["type"] == "TOOL_ACTIVITY"
            and e["payload"].get("toolName") == tool
            and e["payload"].get("activityStatus") == status
        ]

    try:
        robot = launch(
            [
                sys.executable,
                "-m",
                "tangying_sim.server",
                "--listen",
                f"127.0.0.1:{robot_port}",
                "--perception",
                "rgbd",
                "--human-speed",
                "0.01",
            ],
            "runtime.log",
        )
        # The API retries runtime discovery; it never approves until RGB-D exists.
        agent = start_agent("agent-before-restart.log")
        initial = api("/v1/telemetry?adapter=mujoco&limit=1")["latest"]
        save("initial-observation.json", initial)
        assert initial["robotState"]["perception"]["mode"] == "rgbd"
        assert initial["reconstruction"]["sourceType"] == "rgbd_camera"
        assert initial["colorFrameAvailable"] and initial["depthFrameAvailable"]
        task = api(
            "/v1/tasks",
            "POST",
            {
                "adapter": "mujoco",
                "request": "把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来",
            },
        )
        task_path = f"/v1/tasks/{task['id']}"
        api(task_path + "/approve", "POST")

        def picking():
            current = api(task_path)
            assert current["state"] not in TERMINAL, current
            return activities(current, "manipulation.pick", "RUNNING")

        wait_for(picking)
        if scenario == "unknown-outcome":
            # Interrupt the Agent while the deliberately slowed pick is in flight.
            # This is not the safe-pause path: no completed receipt may be inferred.
            time.sleep(0.1)
            agent.kill()
            agent.wait(timeout=5)
            agent = start_agent("agent-after-crash.log")
            recovery = api(task_path + "/recovery")
            save("crash-recovery.json", recovery)
            assert recovery["requiresReconciliation"] and not recovery["canResume"], recovery
            try:
                api(task_path + "/resume", "POST")
            except AssertionError as exc:
                assert "409" in str(exc) and "PHYSICAL_OUTCOME_UNKNOWN" in str(exc), exc
            else:
                raise AssertionError("unknown physical action was allowed to resume")
            final = api(task_path)
            save("blocked-task.json", final)
            assert len(activities(final, "manipulation.pick", "RUNNING")) == 1
            assert not activities(final, "manipulation.place", "RUNNING")
            summary = {
                "passed": True,
                "scenario": scenario,
                "taskId": final["id"],
                "resumeRejected": True,
                "pickDispatches": 1,
                "placeDispatches": 0,
                "physicalHardwareTested": False,
            }
            save("summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return
        api(task_path + "/pause", "POST")

        def paused():
            current = api(task_path)
            assert current["state"] not in TERMINAL, current
            return current if current["state"] == "PAUSED" else None

        paused_task = wait_for(paused)
        assert len(activities(paused_task, "manipulation.pick", "CONFIRMED")) == 1
        assert not activities(paused_task, "manipulation.place", "RUNNING")
        save("paused-task.json", paused_task)
        save("paused-recovery.json", api(task_path + "/recovery"))
        stop(agent)
        agent = start_agent("agent-after-restart.log")
        reopened = api(task_path + "/recovery")
        save("reopened-recovery.json", reopened)
        assert reopened["canResume"] and not reopened["requiresReconciliation"], reopened
        assert api(task_path)["state"] == "PAUSED"
        api(task_path + "/resume", "POST")

        def finished():
            current = api(task_path)
            if current["state"] in TERMINAL:
                return current
            return None

        final = wait_for(finished)
        save("final-task.json", final)
        assert final["state"] == "SUCCEEDED", final
        assert final["currentRevision"] == paused_task["currentRevision"]
        for tool in ("manipulation.pick", "manipulation.place"):
            assert len(activities(final, tool, "RUNNING")) == 2, (tool, final)
            assert len(activities(final, tool, "CONFIRMED")) == 2, (tool, final)
        # Command evidence no longer rewinds the live camera. Wait for the
        # background sensor to independently observe the final scene.
        def observed_final_scene():
            frame = api("/v1/telemetry?adapter=mujoco&limit=1")["latest"]
            found = {e["entityId"]: e.get("relation") for e in frame["reconstruction"]["entities"]}
            return frame if found.get("red-cup") == "inside:right-bin" and found.get("blue-bottle") == "inside:front-tray" else None
        final_view = wait_for(observed_final_scene, 10)
        save("final-observation.json", final_view)
        relations = {
            e["entityId"]: e.get("relation") for e in final_view["reconstruction"]["entities"]
        }
        assert relations["red-cup"] == "inside:right-bin", relations
        assert relations["blue-bottle"] == "inside:front-tray", relations

        # Read immutable, task-scoped historical images, not two unrelated live requests.
        evidence = api(task_path + "/observations")
        save("observation-index.json", evidence)
        records = evidence if isinstance(evidence, list) else evidence["records"]
        linked_ids = {
            i
            for e in final["events"]
            if e["type"] == "TOOL_ACTIVITY"
            for i in e["payload"].get("evidenceIds", [])
        }
        assert linked_ids and linked_ids <= {row["captureId"] for row in records}
        original_checks = []
        for event in final["events"]:
            payload = event.get("payload", {})
            if payload.get("activityStatus") != "CONFIRMED" or payload.get("toolName") != "verify_placement":
                continue
            assert payload.get("evidenceSource") == "command_observation", payload
            assert payload.get("evidenceIds") == [payload["receiptObservationId"]], payload
            record = next(row for row in records if row["captureId"] == payload["receiptObservationId"])
            detail = api(task_path + f"/observations/{record['id']}")
            verification = detail["snapshot"]["robotState"]["verification"]
            assert verification["passed"] and verification["sample_count"] == 3, verification
            assert verification["stable_duration_s"] >= .1 and verification["max_displacement_m"] <= .008, verification
            assert verification["observation_id"] == record["captureId"], verification
            original_checks.append(verification)
        assert len(original_checks) == 2
        save("original-placement-checks.json", original_checks)
        last = max(records, key=lambda row: row["recordIndex"])
        snapshot = api(task_path + f"/observations/{last['id']}")
        save("historical-observation.json", snapshot)
        for kind in ("rgb", "depth"):
            with request.urlopen(
                base + task_path + f"/observations/{last['id']}/{kind}", timeout=10
            ) as response:
                raw = response.read()
                assert response.headers.get_content_type() == "image/png"
                assert raw.startswith(b"\x89PNG\r\n\x1a\n")
            (output / f"historical-{kind}.png").write_bytes(raw)
            assert hashlib.sha256(raw).hexdigest() == last[f"{kind}Sha256"]
        summary = {
            "passed": True,
            "taskId": final["id"],
            "revision": final["currentRevision"],
            "agentRestarted": True,
            "runtimeRestarted": False,
            "pickCalls": 2,
            "placeCalls": 2,
            "finalRelations": relations,
            "linkedCaptureCount": len(linked_ids),
            "historicalFrames": len(records),
            "sourceType": "rgbd_camera",
            "originalPlacementChecks": original_checks,
            "physicalHardwareTested": False,
        }
        save("summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        stop(agent)
        stop(robot)
        for log in logs:
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory")
    parser.add_argument("--agent", type=Path, default=ROOT / "bin/local-agent")
    parser.add_argument(
        "--scenario", choices=("pause-restart", "unknown-outcome"), default="pause-restart"
    )
    args = parser.parse_args()
    run(args.output.resolve(), args.agent.resolve(), args.scenario)
