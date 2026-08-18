from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from contextlib import closing
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/sim-stack.sh"
MAKEFILE = REPO / "Makefile"


def _free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(*arguments: str, env: dict[str, str], check: bool = False):
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=check,
    )


def _matching_processes(fragment: str) -> list[int]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="], text=True, capture_output=True, check=True
    )
    matches = []
    for line in result.stdout.splitlines():
        pid, _, command = line.strip().partition(" ")
        if fragment in command:
            matches.append(int(pid))
    return matches


@pytest.fixture
def stack_env(tmp_path: Path):
    env = os.environ.copy()
    env.update(
        {
            "SIM_STACK_ARTIFACTS_DIR": str(tmp_path / "stack"),
            "SIM_STACK_SIM_PORT": str(_free_port()),
            "SIM_STACK_AGENT_PORT": str(_free_port()),
            "SIM_STACK_STARTUP_TIMEOUT": "4",
        }
    )
    yield env
    if SCRIPT.exists():
        _run("stop", env=env)


def test_supervisor_contract_is_exact_pid_and_loopback_only():
    content = SCRIPT.read_text()
    assert "127.0.0.1" in content
    assert "50051" in content and "8787" in content
    assert "mujoco" in content and "RuntimeInfo" in content
    assert "kill -0" in content and "ps" in content
    assert "pkill" not in content and "killall" not in content
    assert "healthz" in content and "/v1/runtime" in content and "rollback" in content
    assert "local-agent.pid" in content and "mujoco.pid" in content
    assert "wait_for_ports_free" in content
    assert "sim-restart:" in MAKEFILE.read_text()


def test_start_status_are_idempotent_and_stop_removes_only_recorded_children(stack_env):
    first = _run("start", env=stack_env)
    assert first.returncode == 0, first.stdout + first.stderr
    second = _run("start", env=stack_env)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "already running" in second.stdout.lower()

    status = _run("status", env=stack_env)
    assert status.returncode == 0, status.stdout + status.stderr
    assert "mujoco" in status.stdout.lower() and "local-agent" in status.stdout.lower()
    assert "healthy" in status.stdout.lower()

    run_dir = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run"
    pids = [int((run_dir / name).read_text().strip()) for name in ("mujoco.pid", "local-agent.pid")]
    stopped = _run("stop", env=stack_env)
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert not list(run_dir.glob("*.pid"))
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_port_conflict_is_reported_without_signalling_foreign_process(stack_env):
    port = int(stack_env["SIM_STACK_SIM_PORT"])
    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    listener.listen()
    try:
        result = _run("start", env=stack_env)
        assert result.returncode != 0
        assert "port" in (result.stdout + result.stderr).lower()
        assert listener.fileno() >= 0
    finally:
        listener.close()


def test_partial_startup_failure_rolls_back_recorded_mujoco_process(stack_env, tmp_path):
    failing_agent = tmp_path / "failing-local-agent"
    failing_agent.write_text("#!/usr/bin/env bash\nexit 23\n")
    failing_agent.chmod(0o755)
    stack_env["SIM_STACK_LOCAL_AGENT"] = str(failing_agent)

    result = _run("start", env=stack_env)
    assert result.returncode != 0
    run_dir = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run"
    assert not list(run_dir.glob("*.pid"))
    assert "rollback" in (result.stdout + result.stderr).lower()


def test_stop_refuses_to_signal_pid_when_recorded_identity_does_not_match(stack_env):
    run_dir = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run"
    run_dir.mkdir(parents=True)
    foreign = subprocess.Popen(["sleep", "20"])
    try:
        (run_dir / "mujoco.pid").write_text(f"{foreign.pid}\n")
        (run_dir / "mujoco.identity").write_text("definitely-not-sleep\n")
        result = _run("stop", env=stack_env)
        assert result.returncode != 0
        assert foreign.poll() is None
        assert "identity" in (result.stdout + result.stderr).lower()
    finally:
        foreign.send_signal(signal.SIGTERM)
        foreign.wait(timeout=5)


def test_foreground_mode_is_supported_without_changing_background_default():
    content = SCRIPT.read_text()
    assert "--foreground" in content and "--background" in content
    assert "SIM_STACK_SIM_PORT" in content
    assert "SIM_STACK_AGENT_PORT" in content


def test_foreground_signal_cleans_children_even_during_readiness(stack_env):
    process = subprocess.Popen(
        ["bash", str(SCRIPT), "start", "--foreground"],
        cwd=REPO,
        env=stack_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    agent_port = int(stack_env["SIM_STACK_AGENT_PORT"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", agent_port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
    else:
        process.kill()
        raise AssertionError("foreground Local Agent did not open its port")

    process.send_signal(signal.SIGTERM)
    process.wait(timeout=10)
    run_dir = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run"
    assert not list(run_dir.glob("*.pid"))
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", int(stack_env["SIM_STACK_SIM_PORT"])), timeout=0.2)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", agent_port), timeout=0.2)


def test_two_consecutive_restarts_wait_for_ports_and_remain_healthy(stack_env):
    started = _run("start", env=stack_env)
    assert started.returncode == 0, started.stdout + started.stderr
    for _ in range(2):
        restarted = _run("restart", env=stack_env)
        assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    status = _run("status", env=stack_env)
    assert status.returncode == 0, status.stdout + status.stderr
    assert status.stdout.lower().count("healthy") == 2


@pytest.mark.parametrize("record_name", ["mujoco.pid", "local-agent.pid"])
def test_pid_record_failure_rolls_back_known_child_without_a_record(
    stack_env, tmp_path, record_name
):
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        "#!/usr/bin/env bash\n"
        "target=\"${@: -1}\"\n"
        f"if [[ \"$target\" == */{record_name} ]]; then exit 77; fi\n"
        "exec /bin/mv \"$@\"\n"
    )
    fake_mv.chmod(0o755)
    stack_env["PATH"] = f"{fake_bin}:{stack_env['PATH']}"
    fragments = [
        f"tangying_sim.server --listen 127.0.0.1:{stack_env['SIM_STACK_SIM_PORT']}",
        f"local-agent --dev-insecure --listen 127.0.0.1:{stack_env['SIM_STACK_AGENT_PORT']}",
    ]

    try:
        result = _run("start", env=stack_env)
        assert result.returncode != 0
        assert "record" in (result.stdout + result.stderr).lower()
        assert all(_matching_processes(fragment) == [] for fragment in fragments)
        run_dir = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run"
        assert not list(run_dir.glob("*.pid"))
    finally:
        for fragment in fragments:
            for pid in _matching_processes(fragment):
                os.kill(pid, signal.SIGTERM)


def test_start_rejects_artifacts_path_that_cannot_be_created(stack_env, tmp_path):
    artifacts_file = tmp_path / "not-a-directory"
    artifacts_file.write_text("occupied")
    stack_env["SIM_STACK_ARTIFACTS_DIR"] = str(artifacts_file)
    fragment = f"tangying_sim.server --listen 127.0.0.1:{stack_env['SIM_STACK_SIM_PORT']}"

    result = _run("start", env=stack_env)
    assert result.returncode != 0
    assert "artifacts" in (result.stdout + result.stderr).lower()
    assert _matching_processes(fragment) == []
