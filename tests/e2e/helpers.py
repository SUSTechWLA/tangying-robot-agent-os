from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

REPO = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def port_is_available(port: int) -> bool:
    try:
        with closing(socket.socket()) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def json_request(url: str, method: str = "GET", body: dict | None = None):
    payload = None if body is None else json.dumps(body).encode()
    req = request.Request(url, data=payload, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    with request.urlopen(req, timeout=10) as response:
        return json.load(response)


@dataclass
class IsolatedSimulationStack:
    """Exact-PID stack wrapper for live tests on isolated loopback ports."""

    agent_port: int
    robot_port: int
    artifacts_dir: Path
    local_agent: Path
    seed: int = 7

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.agent_port}"

    def _command(self, operation: str) -> list[str]:
        return [
            "bash",
            str(REPO / "scripts/sim-stack.sh"),
            operation,
            "--sim-port",
            str(self.robot_port),
            "--agent-port",
            str(self.agent_port),
            "--artifacts-dir",
            str(self.artifacts_dir),
        ]

    def run_lifecycle(self, operation: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["SIM_STACK_LOCAL_AGENT"] = str(self.local_agent)
        environment["SIM_STACK_SEED"] = str(self.seed)
        return subprocess.run(
            self._command(operation),
            cwd=REPO,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=35,
        )

    def recorded_pids(self) -> tuple[int, ...]:
        result = []
        for name in ("mujoco.pid", "local-agent.pid"):
            path = self.artifacts_dir / "run" / name
            if path.exists():
                result.append(int(path.read_text().strip()))
        return tuple(result)

    def stop_and_assert_clean(self) -> None:
        pids = self.recorded_pids()
        stopped = self.run_lifecycle("stop")
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if all(not _pid_is_alive(pid) for pid in pids) and all(
                port_is_available(port) for port in (self.agent_port, self.robot_port)
            ):
                break
            time.sleep(0.05)
        assert all(not _pid_is_alive(pid) for pid in pids), f"stack PIDs survived stop: {pids}"
        assert port_is_available(self.agent_port)
        assert port_is_available(self.robot_port)
        assert self.recorded_pids() == ()

    def get_json(self, path: str) -> dict:
        return json_request(self.base_url + path)

    def get_bytes(self, path: str) -> tuple[bytes, str]:
        with request.urlopen(self.base_url + path, timeout=10) as response:
            return response.read(), response.headers.get_content_type()

    def wait_for_telemetry(self, timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout
        latest: dict = {}
        while time.monotonic() < deadline:
            try:
                telemetry = self.get_json("/v1/telemetry?adapter=mujoco&limit=1")
                if telemetry.get("hasLatest"):
                    return telemetry
                latest = telemetry
            except (OSError, ValueError, error.HTTPError):
                pass
            time.sleep(0.1)
        raise AssertionError(f"startup telemetry unavailable: {latest}")

    def run_task(self, request_text: str, timeout: float = 60.0) -> dict:
        task = json_request(
            self.base_url + "/v1/tasks",
            "POST",
            {"request": request_text, "adapter": "mujoco"},
        )
        json_request(self.base_url + f"/v1/tasks/{task['id']}/approve", "POST")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.get_json(f"/v1/tasks/{task['id']}")
            if task["state"] in {
                "FAILED",
                "CANCELLED",
                "RECOVERABLE_FAILURE",
                "SAFETY_STOPPED",
            }:
                return task
            if task["state"] == "SUCCEEDED" and any(
                event["type"] == "LOCAL_RUN_SUCCEEDED" for event in task.get("events", [])
            ):
                return task
            time.sleep(0.05)
        raise AssertionError(f"task did not finish: {task}")


def start_isolated_simulation_stack(tmp_path: Path, *, seed: int = 7) -> IsolatedSimulationStack:
    local_agent = tmp_path / "bin/local-agent"
    local_agent.parent.mkdir(parents=True, exist_ok=True)
    built = subprocess.run(
        ["go", "build", "-o", str(local_agent), "./cmd/local-agent"],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert built.returncode == 0, built.stderr

    failures = []
    for attempt in range(5):
        agent_port = free_port()
        robot_port = free_port()
        while robot_port == agent_port:
            robot_port = free_port()
        stack = IsolatedSimulationStack(
            agent_port=agent_port,
            robot_port=robot_port,
            artifacts_dir=tmp_path / f"stack-{attempt}",
            local_agent=local_agent,
            seed=seed,
        )
        started = stack.run_lifecycle("start")
        if started.returncode == 0:
            return stack
        failures.append(started.stdout + started.stderr)
        stack.run_lifecycle("stop")
        if not _retryable_port_race(stack, failures[-1]):
            break
    raise AssertionError("isolated stack failed to start:\n" + "\n--- retry ---\n".join(failures))


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _retryable_port_race(stack: IsolatedSimulationStack, output: str) -> bool:
    evidence = output.lower()
    for path in (
        stack.artifacts_dir / "logs" / "mujoco.log",
        stack.artifacts_dir / "logs" / "local-agent.log",
    ):
        if path.exists():
            evidence += "\n" + path.read_text(errors="replace").lower()
    return any(
        marker in evidence
        for marker in ("already occupied", "address already in use", "failed to bind")
    )


def run_simulation_task(request_text: str, tmp_path: Path, *, seed: int = 7) -> dict:
    stack = start_isolated_simulation_stack(tmp_path, seed=seed)
    try:
        stack.wait_for_telemetry()
        finished = stack.run_task(request_text)
        assert finished["state"] == "SUCCEEDED", json.dumps(finished)
        deadline = time.monotonic() + 10
        telemetry = stack.get_json("/v1/telemetry?adapter=mujoco")
        while (
            telemetry.get("latest", {}).get("activity") != "IDLE"
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
            telemetry = stack.get_json("/v1/telemetry?adapter=mujoco")
        assert telemetry["hasLatest"] is True, json.dumps(telemetry)
        finished["telemetry"] = telemetry
        return finished
    finally:
        stack.stop_and_assert_clean()
