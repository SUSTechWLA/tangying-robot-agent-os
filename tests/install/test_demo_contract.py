from __future__ import annotations

import json
import os
import signal
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_demo_is_bounded_to_loopback_and_always_cleans_up():
    script = (ROOT / "scripts/demo.sh").read_text()
    assert "trap cleanup EXIT INT TERM" in script
    assert "127.0.0.1" in script
    assert "mktemp -d" in script
    assert "--dev-insecure" in script
    assert "SUCCEEDED" in script


def test_demo_check_mode_validates_without_starting_stack():
    completed = subprocess.run(
        ["bash", "scripts/demo.sh", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "demo prerequisites: OK" in completed.stdout


def test_demo_runs_only_the_local_agent_and_robot_runtime():
    script = (ROOT / "scripts/demo.sh").read_text()
    assert "./cmd/local-agent" in script
    assert "tangying_sim.server" in script
    assert "cloud-control-plane" not in script
    assert "--cloud" not in script


def test_demo_exit_reaps_the_actual_local_agent_process(tmp_path):
    _run_demo_and_check_cleanup(tmp_path)


def _run_demo_and_check_cleanup(tmp_path, *, extra_environment=None, succeeds=True):
    environment = dict(os.environ, TMPDIR=str(tmp_path))
    environment.update(extra_environment or {})
    completed = subprocess.run(
        ["bash", "scripts/demo.sh"], cwd=ROOT, env=environment,
        text=True, capture_output=True, timeout=90, check=False,
    )
    fragment = f"{tmp_path}/tangying-robot-demo."
    processes = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
    remaining = []
    for line in processes.splitlines():
        pid, _, command = line.strip().partition(" ")
        if fragment in command and "--data-dir" in command:
            remaining.append((int(pid), command.strip()))
    try:
        if succeeds:
            assert completed.returncode == 0, completed.stdout + completed.stderr
            assert "demo succeeded" in completed.stdout
        else:
            assert completed.returncode == 1, completed.stdout + completed.stderr
            assert "demo succeeded" not in completed.stdout
        assert remaining == [], "demo left its Local Agent child running"
        if extra_environment:
            runtime_pid = int((tmp_path / "runtime-pid").read_text())
            process = subprocess.run(
                ["ps", "-p", str(runtime_pid), "-o", "command="],
                text=True, capture_output=True, check=False,
            )
            assert "tangying_sim.server" not in process.stdout, "demo left its Runtime child running"
    finally:
        # A failing regression must still clean only its own exact child.
        for pid, expected in remaining:
            current = subprocess.run(
                ["ps", "-ww", "-p", str(pid), "-o", "command="],
                text=True, capture_output=True, check=False,
            ).stdout.strip()
            if current == expected:
                os.kill(pid, signal.SIGTERM)
    return completed


def _runtime_fault_environment(tmp_path, mode):
    # Python startup hooks affect only this test's real Runtime child. The
    # console, gRPC client, MuJoCo world and task executor remain unchanged.
    hooks = tmp_path / "python-hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(textwrap.dedent('''\
        import json
        import os
        import re
        import sys
        import threading
        import time
        from pathlib import Path
        from urllib.request import ProxyHandler, build_opener

        if "tangying_sim.server" in sys.orig_argv:
            root = Path(os.environ["TMPDIR"])
            (root / "runtime-pid").write_text(str(os.getpid()))
            mode = os.environ["DEMO_TEST_RUNTIME_FAULT"]
            if mode == "bootstrap":
                time.sleep(2)
                (root / "fault-applied").write_text(mode)
            import grpc
            original = grpc.unary_stream_rpc_method_handler
            first_observation = True
            lock = threading.Lock()

            def handler(behavior, *args, **kwargs):
                def wrapped(request, context):
                    global first_observation
                    with lock:
                        delay = behavior.__name__ == "Observe" and first_observation
                        if delay:
                            first_observation = False
                    if mode == "observation" and delay:
                        time.sleep(2)
                        log = next(root.glob("tangying-robot-demo.*/local-agent.log"))
                        url = re.search(r"http://127[.]0[.]0[.]1:[0-9]+", log.read_text())[0]
                        with build_opener(ProxyHandler({})).open(url + "/v1/tasks", timeout=2) as response:
                            tasks = json.load(response)
                        (root / "fault-applied").write_text(json.dumps({"tasksBeforeObservation": len(tasks)}))
                    if mode == "execution" and behavior.__name__ == "ExecuteSkill":
                        (root / "fault-applied").write_text(mode)
                        context.abort(grpc.StatusCode.UNAVAILABLE, "controlled demo regression failure")
                    yield from behavior(request, context)
                return original(wrapped, *args, **kwargs)

            grpc.unary_stream_rpc_method_handler = handler
    '''))
    return {
        "PYTHONPATH": os.pathsep.join(filter(None, [str(hooks), os.environ.get("PYTHONPATH")])),
        "DEMO_TEST_RUNTIME_FAULT": mode,
    }


@pytest.mark.parametrize("mode", ["bootstrap", "observation"])
def test_demo_waits_for_real_runtime_and_observation(tmp_path, mode):
    environment = _runtime_fault_environment(tmp_path, mode)
    _run_demo_and_check_cleanup(tmp_path, extra_environment=environment)
    applied = (tmp_path / "fault-applied").read_text()
    if mode == "observation":
        assert json.loads(applied)["tasksBeforeObservation"] == 0
    else:
        assert applied == "bootstrap"


def test_demo_reports_recoverable_failure_and_cleans_up(tmp_path):
    environment = _runtime_fault_environment(tmp_path, "execution")
    completed = _run_demo_and_check_cleanup(tmp_path, extra_environment=environment, succeeds=False)
    assert (tmp_path / "fault-applied").read_text() == "execution"
    assert "demo task ended in RECOVERABLE_FAILURE" in completed.stderr
    assert "demo task did not finish" not in completed.stderr
