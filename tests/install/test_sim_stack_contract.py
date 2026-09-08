from __future__ import annotations

import os
import shlex
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/sim-stack.sh"
MAKEFILE = REPO / "Makefile"


def _free_ports(count: int = 2) -> list[int]:
    sockets = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


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


def _process_birth(pid: int) -> str:
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        rest = proc_stat.read_text().rsplit(") ", 1)[1].split()
        return f"linux:{rest[19]}"
    started = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.split()
    return f"darwin:{' '.join(started)}"


def _write_identity(run_dir: Path, service: str, process: subprocess.Popen, argv: str):
    executable = str(Path(process.args[0]).resolve())
    (run_dir / f"{service}.pid").write_text(f"{process.pid}\n")
    (run_dir / f"{service}.identity").write_text(
        f"BIRTH={_process_birth(process.pid)}\n"
        f"EXECUTABLE={executable}\n"
        f"ARGV={argv}\n"
    )


@pytest.fixture
def stack_env(tmp_path: Path):
    sim_port, agent_port = _free_ports()
    env = os.environ.copy()
    env.update(
        {
            "SIM_STACK_ARTIFACTS_DIR": str(tmp_path / "stack"),
            "SIM_STACK_SIM_PORT": str(sim_port),
            "SIM_STACK_AGENT_PORT": str(agent_port),
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


@pytest.fixture(scope="module")
def compiled_local_agent(tmp_path_factory):
    binary = tmp_path_factory.mktemp("rgbd-lifecycle-binary") / "local-agent"
    subprocess.run(
        ["go", "build", "-o", str(binary), "./cmd/local-agent"],
        cwd=REPO, text=True, capture_output=True, timeout=60, check=True,
    )
    return str(binary)


def _assert_stack_perception(stack_env, perception):
    import grpc
    from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

    run_dir = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run"
    metadata = dict(line.split("=", 1) for line in (run_dir / "stack.env").read_text().splitlines())
    assert metadata["PERCEPTION"] == perception
    for service, flag, value in (
        ("mujoco", "--perception", perception),
        ("local-agent", "--robot-safety-profile", "simulation"),
    ):
        pid = int((run_dir / f"{service}.pid").read_text())
        argv = shlex.split(subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            text=True, capture_output=True, check=True,
        ).stdout)
        assert argv[argv.index(flag) + 1] == value
    with grpc.insecure_channel(f"127.0.0.1:{metadata['SIM_PORT']}") as channel:
        info = robot_pb2_grpc.RobotRuntimeStub(channel).GetRuntimeInfo(
            robot_pb2.GetRuntimeInfoRequest(), timeout=2,
        )
    if perception == "rgbd":
        assert info.robot_profile["modelId"] == "xlerobot-rgbd-reference"
        assert info.robot_profile["sensors"][0]["sourceType"] == "rgbd_camera"
    else:
        assert not info.robot_profile


@pytest.mark.parametrize("foreground", [False, True], ids=["background", "foreground"])
def test_rgbd_lifecycle_preserves_mode_and_explicit_simulation_safety(
    stack_env, compiled_local_agent, foreground,
):
    stack_env["SIM_STACK_LOCAL_AGENT"] = compiled_local_agent
    stack_env["SIM_STACK_STARTUP_TIMEOUT"] = "12"
    owner = None
    try:
        arguments = ["start", "--perception", "rgbd"]
        if foreground:
            owner = subprocess.Popen(
                ["bash", str(SCRIPT), *arguments, "--foreground"],
                cwd=REPO, env=stack_env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if _run("status", env=stack_env).returncode == 0:
                    break
                if owner.poll() is not None:
                    raise AssertionError(owner.communicate(timeout=1)[0])
                time.sleep(0.05)
            else:
                raise AssertionError("RGB-D foreground stack did not become healthy")
        else:
            started = _run(*arguments, env=stack_env)
            assert started.returncode == 0, started.stdout + started.stderr
        _assert_stack_perception(stack_env, "rgbd")

        # An implicit start reuses the recorded mode; changing a healthy stack
        # requires an explicit restart and must not silently relabel its sensors.
        repeated = _run("start", env=stack_env)
        assert repeated.returncode == 0, repeated.stdout + repeated.stderr
        conflict = _run("start", "--perception", "ground-truth", env=stack_env)
        assert conflict.returncode != 0
        assert "use restart --perception" in conflict.stderr
        _assert_stack_perception(stack_env, "rgbd")

        restarted = _run("restart", env=stack_env)
        assert restarted.returncode == 0, restarted.stdout + restarted.stderr
        if owner is not None:
            owner.communicate(timeout=10)
        _assert_stack_perception(stack_env, "rgbd")

        switched = _run("restart", "--perception", "ground-truth", env=stack_env)
        assert switched.returncode == 0, switched.stdout + switched.stderr
        _assert_stack_perception(stack_env, "ground-truth")
    finally:
        _run("stop", env=stack_env)
        if owner is not None:
            try:
                owner.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                owner.terminate()
                owner.communicate(timeout=10)


def test_invalid_perception_is_rejected_before_starting_children(stack_env):
    result = _run("start", "--perception", "unknown-camera", env=stack_env)
    assert result.returncode != 0
    assert "perception must be rgbd or ground-truth" in result.stderr
    run_dir = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run"
    assert not list(run_dir.glob("*.pid"))


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
    for name in ("mujoco.identity", "local-agent.identity"):
        identity = (run_dir / name).read_text()
        assert "BIRTH=" in identity and "EXECUTABLE=" in identity and "ARGV=" in identity
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


def test_stop_refuses_foreign_process_with_target_prefix_and_extra_argv(stack_env):
    run_dir = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run"
    run_dir.mkdir(parents=True)
    foreign = subprocess.Popen(
        [
            str(REPO / ".venv/bin/python"),
            "-c",
            "import time; time.sleep(20)",
            "--foreign-argv-suffix",
        ]
    )
    try:
        command = subprocess.run(
            ["ps", "-p", str(foreign.pid), "-o", "command="],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        prefix = command.removesuffix(" --foreign-argv-suffix")
        _write_identity(run_dir, "mujoco", foreign, prefix)
        result = _run("stop", env=stack_env)
        assert result.returncode != 0
        assert foreign.poll() is None
        assert (run_dir / "mujoco.pid").exists()
    finally:
        if foreign.poll() is None:
            foreign.send_signal(signal.SIGTERM)
        foreign.wait(timeout=5)


def test_kill_escalation_retains_record_until_identity_disappears(stack_env):
    run_dir = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run"
    run_dir.mkdir(parents=True)
    foreign = subprocess.Popen(
        [
            str(REPO / ".venv/bin/python"),
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(20)",
        ]
    )
    try:
        time.sleep(0.1)
        argv = subprocess.run(
            ["ps", "-ww", "-p", str(foreign.pid), "-o", "command="],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        _write_identity(run_dir, "mujoco", foreign, argv)
        result = _run("stop", env=stack_env)
        assert result.returncode != 0
        assert "retaining process record" in (result.stdout + result.stderr)
        assert (run_dir / "mujoco.pid").exists()
        assert (run_dir / "mujoco.identity").exists()
        assert foreign.wait(timeout=10) < 0
        stopped = _run("stop", env=stack_env)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert not (run_dir / "mujoco.pid").exists()
        assert not (run_dir / "mujoco.identity").exists()
    finally:
        if foreign.poll() is None:
            foreign.kill()
            foreign.wait(timeout=5)


def test_term_exec_with_same_birth_retains_record_and_does_not_kill_replacement(stack_env):
    run_dir = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run"
    run_dir.mkdir(parents=True)
    foreign = subprocess.Popen(
        [
            str(REPO / ".venv/bin/python"),
            "-c",
            (
                "import os,signal,time; "
                "signal.signal(signal.SIGTERM, lambda *_: os.execv('/bin/sleep', ['/bin/sleep', '20'])); "
                "time.sleep(20)"
            ),
        ]
    )
    try:
        time.sleep(0.1)
        argv = subprocess.run(
            ["ps", "-ww", "-p", str(foreign.pid), "-o", "command="],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        _write_identity(run_dir, "mujoco", foreign, argv)
        result = _run("stop", env=stack_env)
        assert result.returncode != 0
        assert foreign.poll() is None
        replacement = subprocess.run(
            ["ps", "-ww", "-p", str(foreign.pid), "-o", "command="],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        assert replacement == "/bin/sleep 20"
        assert (run_dir / "mujoco.pid").exists()
        assert (run_dir / "mujoco.identity").exists()
        assert "identity changed" in (result.stdout + result.stderr).lower()
    finally:
        if foreign.poll() is None:
            foreign.kill()
        foreign.wait(timeout=5)


def test_foreground_mode_is_supported_without_changing_background_default():
    content = SCRIPT.read_text()
    assert "--foreground" in content and "--background" in content
    assert "SIM_STACK_SIM_PORT" in content
    assert "SIM_STACK_AGENT_PORT" in content


def test_background_stack_survives_short_lived_parent_session_teardown(stack_env):
    parent = subprocess.Popen(
        ["bash", str(SCRIPT), "start"],
        cwd=REPO,
        env=stack_env,
        start_new_session=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output, _ = parent.communicate(timeout=20)
    assert parent.returncode == 0, output

    try:
        os.killpg(parent.pid, signal.SIGHUP)
    except ProcessLookupError:
        # A correctly detached stack has no members left in the parent group.
        pass
    time.sleep(0.2)

    status = _run("status", env=stack_env)
    assert status.returncode == 0, status.stdout + status.stderr
    run_dir = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run"
    child_pids = [
        int((run_dir / name).read_text())
        for name in ("mujoco.pid", "local-agent.pid")
    ]
    for child_pid in child_pids:
        details = subprocess.run(
            ["ps", "-p", str(child_pid), "-o", "pgid="],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        assert int(details) != parent.pid


def test_foreground_stack_remains_attached_and_cleans_up_on_session_hup(stack_env):
    foreground = subprocess.Popen(
        ["bash", str(SCRIPT), "start", "--foreground"],
        cwd=REPO,
        env=stack_env,
        start_new_session=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _run("status", env=stack_env).returncode == 0:
            break
        time.sleep(0.05)
    else:
        foreground.kill()
        raise AssertionError("foreground stack did not become healthy")

    os.killpg(foreground.pid, signal.SIGHUP)
    foreground.wait(timeout=10)
    run_dir = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run"
    assert not list(run_dir.glob("*.pid"))
    assert _run("status", env=stack_env).returncode != 0


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


def test_replaced_foreground_generation_does_not_stop_restarted_stack(stack_env):
    foreground = subprocess.Popen(
        ["bash", str(SCRIPT), "start", "--foreground"],
        cwd=REPO,
        env=stack_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    metadata = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run" / "stack.env"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if metadata.exists() and _run("status", env=stack_env).returncode == 0:
            break
        time.sleep(0.05)
    else:
        foreground.kill()
        raise AssertionError("foreground stack did not become healthy")
    first_generation = metadata.read_text()

    restarted = _run("restart", env=stack_env)
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    foreground.wait(timeout=10)
    second_generation = metadata.read_text()
    assert second_generation != first_generation
    status = _run("status", env=stack_env)
    assert status.returncode == 0, status.stdout + status.stderr


def test_two_consecutive_restarts_wait_for_ports_and_remain_healthy(stack_env):
    started = _run("start", env=stack_env)
    assert started.returncode == 0, started.stdout + started.stderr
    for _ in range(2):
        restarted = _run("restart", env=stack_env)
        assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    status = _run("status", env=stack_env)
    assert status.returncode == 0, status.stdout + status.stderr
    assert status.stdout.lower().count("healthy") == 2


def test_restart_without_overrides_reuses_recorded_ports_and_seed(stack_env):
    sim_port = stack_env.pop("SIM_STACK_SIM_PORT")
    agent_port = stack_env.pop("SIM_STACK_AGENT_PORT")
    started = _run(
        "start",
        "--sim-port",
        sim_port,
        "--agent-port",
        agent_port,
        "--seed",
        "19",
        env=stack_env,
    )
    assert started.returncode == 0, started.stdout + started.stderr
    restarted = _run("restart", env=stack_env)
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    metadata = (
        Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run" / "stack.env"
    ).read_text()
    assert f"SIM_PORT={sim_port}" in metadata
    assert f"AGENT_PORT={agent_port}" in metadata
    assert "SEED=19" in metadata
    assert _run("status", env=stack_env).returncode == 0


def _concurrent(*commands: list[str], env: dict[str, str]):
    processes = [
        subprocess.Popen(
            ["bash", str(SCRIPT), *command],
            cwd=REPO,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for command in commands
    ]
    return [(process.wait(timeout=20), process.stdout.read()) for process in processes]


def test_concurrent_starts_serialize_and_are_idempotent(stack_env):
    results = _concurrent(["start"], ["start"], env=stack_env)
    assert [code for code, _ in results] == [0, 0], results
    assert _run("status", env=stack_env).returncode == 0


def test_concurrent_start_and_stop_leave_stack_stopped(stack_env):
    start = subprocess.Popen(
        ["bash", str(SCRIPT), "start"],
        cwd=REPO,
        env=stack_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    lock = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run" / "lifecycle.lock"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not lock.exists():
        time.sleep(0.01)
    assert lock.exists(), "start did not acquire the lifecycle lock"
    stop = subprocess.Popen(
        ["bash", str(SCRIPT), "stop"],
        cwd=REPO,
        env=stack_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    results = [
        (start.wait(timeout=20), start.stdout.read()),
        (stop.wait(timeout=20), stop.stdout.read()),
    ]
    assert [code for code, _ in results] == [0, 0], results
    assert _run("status", env=stack_env).returncode != 0
    assert not _matching_processes(
        f"tangying_sim.server --listen 127.0.0.1:{stack_env['SIM_STACK_SIM_PORT']}"
    )


def test_concurrent_restarts_serialize_without_partial_state(stack_env):
    assert _run("start", env=stack_env).returncode == 0
    results = _concurrent(["restart"], ["restart"], env=stack_env)
    assert [code for code, _ in results] == [0, 0], results
    assert _run("status", env=stack_env).returncode == 0


def test_lifecycle_wait_covers_shutdown_before_startup_budget(stack_env):
    stack_env["SIM_STACK_STARTUP_TIMEOUT"] = "1"
    stack_env["SIM_STACK_STOP_TIMEOUT"] = "3"
    lock = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run" / "lifecycle.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text(f"PID={os.getpid()}\nBIRTH={_process_birth(os.getpid())}\n")

    def finish_slow_lifecycle_phase():
        # A valid owner can spend more than the startup budget in shutdown.
        time.sleep(2)
        (lock / "owner").unlink()
        lock.rmdir()

    owner = threading.Thread(target=finish_slow_lifecycle_phase)
    owner.start()
    try:
        result = _run("stop", env=stack_env)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        owner.join(timeout=5)


def test_explicit_lifecycle_wait_timeout_does_not_signal_live_lock_owner(stack_env):
    stack_env["SIM_STACK_LOCK_TIMEOUT"] = "1"
    lock = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run" / "lifecycle.lock"
    lock.mkdir(parents=True)
    owner = f"PID={os.getpid()}\nBIRTH={_process_birth(os.getpid())}\n"
    (lock / "owner").write_text(owner)

    def finish_lifecycle_phase():
        time.sleep(2)
        (lock / "owner").unlink()
        lock.rmdir()

    releasing_owner = threading.Thread(target=finish_lifecycle_phase)
    releasing_owner.start()
    try:
        result = _run("stop", env=stack_env)
        assert result.returncode == 1
        assert "timed out waiting for lifecycle lock" in result.stderr
        assert (lock / "owner").read_text() == owner
    finally:
        releasing_owner.join(timeout=5)


@pytest.mark.parametrize("timeout", ["0", "invalid"])
def test_invalid_lifecycle_wait_budget_is_rejected(stack_env, timeout):
    stack_env["SIM_STACK_LOCK_TIMEOUT"] = timeout

    result = _run("stop", env=stack_env)

    assert result.returncode == 2
    assert "lifecycle lock timeout must be an integer" in result.stderr


def test_stale_lifecycle_lock_owner_is_recovered_safely(stack_env):
    lock = Path(stack_env["SIM_STACK_ARTIFACTS_DIR"]) / "run" / "lifecycle.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text("PID=99999999\nBIRTH=darwin:stale\n")
    started = _run("start", env=stack_env)
    assert started.returncode == 0, started.stdout + started.stderr
    assert _run("status", env=stack_env).returncode == 0


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
