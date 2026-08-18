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


def json_request(url: str, method: str = "GET", body: dict | None = None):
    payload = None if body is None else json.dumps(body).encode()
    req = request.Request(url, data=payload, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    with request.urlopen(req, timeout=10) as response:
        return json.load(response)


def wait_http(url: str, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json_request(url)
        except (OSError, ValueError):
            time.sleep(0.1)
    raise TimeoutError(url)


@dataclass
class IsolatedSimulationStack:
    """Exact-PID stack wrapper for live tests on isolated loopback ports."""

    agent_port: int
    robot_port: int
    artifacts_dir: Path
    local_agent: Path

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
        return subprocess.run(
            self._command(operation),
            cwd=REPO,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=35,
        )

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


def run_simulation_task(request_text: str, tmp_path: Path, *, seed: int = 7) -> dict:
    local_port = free_port()
    robot_port = free_port()
    env = os.environ.copy()
    env["GOCACHE"] = str(tmp_path / "gocache")
    (tmp_path / "gocache").mkdir(parents=True, exist_ok=True)
    robot = subprocess.Popen(
        [str(REPO / ".venv/bin/python"), "-m", "tangying_sim.server", "--listen", f"127.0.0.1:{robot_port}", "--seed", str(seed)],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    local = subprocess.Popen(
        [
            "go",
            "run",
            "./cmd/local-agent",
            "--dev-insecure",
            "--listen",
            f"127.0.0.1:{local_port}",
            "--robot",
            f"127.0.0.1:{robot_port}",
            "--data-dir",
            str(tmp_path / "local-agent"),
        ],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        base = f"http://127.0.0.1:{local_port}"
        wait_http(base + "/healthz")
        task = json_request(
            base + "/v1/tasks",
            "POST",
            {"request": request_text, "adapter": "mujoco"},
        )
        json_request(base + f"/v1/tasks/{task['id']}/approve", "POST")
        deadline = time.monotonic() + 30
        finished = task
        while time.monotonic() < deadline:
            finished = json_request(base + f"/v1/tasks/{task['id']}")
            if finished["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERABLE_FAILURE"}:
                break
            time.sleep(0.05)
        assert finished["state"] == "SUCCEEDED", json.dumps(finished)
        telemetry = json_request(base + "/v1/telemetry?adapter=mujoco")
        assert telemetry["hasLatest"] is True, json.dumps(telemetry)
        finished["telemetry"] = telemetry
        return finished
    finally:
        for process in (local, robot):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
