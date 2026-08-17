from __future__ import annotations

import json
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


def test_natural_language_reaches_verified_simulation_result(tmp_path):
    cloud_port = free_port()
    robot_port = free_port()
    cloud = subprocess.Popen(
        ["go", "run", "./cmd/cloud-control-plane", "--dev", "--listen", f"127.0.0.1:{cloud_port}"],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    robot = subprocess.Popen(
        [str(REPO / ".venv/bin/python"), "-m", "tangying_sim.server", "--listen", f"127.0.0.1:{robot_port}", "--seed", "7"],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        base = f"http://127.0.0.1:{cloud_port}"
        wait_http(base + "/healthz")
        task = json_request(
            base + "/v1/tasks",
            "POST",
            {"request": "把红色杯子放进右侧收纳盒", "adapter": "mujoco"},
        )
        json_request(base + f"/v1/tasks/{task['id']}/approve", "POST")

        completed = subprocess.run(
            [
                "go",
                "run",
                "./cmd/local-agent",
                "--once",
                "--dev-insecure",
                "--cloud",
                base,
                "--robot",
                f"127.0.0.1:{robot_port}",
                "--data-dir",
                str(tmp_path / "local-agent"),
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

        finished = json_request(base + f"/v1/tasks/{task['id']}")
        assert finished["state"] == "SUCCEEDED", completed.stdout + completed.stderr + json.dumps(finished)
        assert [event["type"] for event in finished["events"]] == [
            "TASK_CREATED",
            "TASK_APPROVED",
            "STATE_CHANGED",
            "STATE_CHANGED",
            "STATE_CHANGED",
            "STATE_CHANGED",
            "STATE_CHANGED",
            "LOCAL_RUN_SUCCEEDED",
        ]
    finally:
        for process in (cloud, robot):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
