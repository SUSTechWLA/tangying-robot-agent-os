from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from contextlib import closing
from pathlib import Path
from urllib import request

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
