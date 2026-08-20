"""Process harness for the cloud-first two-robot handoff system.

The harness deliberately uses public process boundaries: operator HTTP,
device HTTP, the mTLS Fleet Link, and two Robot Runtime gRPC endpoints.  It
does not reach into coordinator or simulator objects, so the same assertions
remain useful when the runtime endpoints are replaced by physical robots.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib import error, request

import pytest

from tests.e2e.helpers import REPO, free_port

HANDOFF_PROMPT = "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区"


def api_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    token: str = "",
    timeout: float = 10,
):
    payload = None if body is None else json.dumps(body).encode()
    call = request.Request(base_url + path, data=payload, method=method)
    if payload is not None:
        call.add_header("Content-Type", "application/json")
    if token:
        call.add_header("Authorization", f"Bearer {token}")
    try:
        with request.urlopen(call, timeout=timeout) as response:
            raw = response.read()
    except error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise AssertionError(f"{method} {path} returned {exc.code}: {detail}") from exc
    return json.loads(raw) if raw else None


def _wait_port(port: int, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as connection:
            connection.settimeout(0.2)
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise AssertionError(f"port {port} did not become reachable")


@dataclass
class FleetHandoffStack:
    tmp: Path
    fleet_port: int
    gateway_port: int
    sim1_port: int
    sim2_port: int
    processes: dict[str, subprocess.Popen] = field(default_factory=dict)
    operator_token: str = ""

    @property
    def fleet_url(self) -> str:
        return f"http://127.0.0.1:{self.fleet_port}"

    @property
    def certs(self) -> Path:
        return self.tmp / "certs"

    def api(self, path: str, *, method: str = "GET", body: dict | None = None):
        return api_json(self.fleet_url, path, method=method, body=body, token=self.operator_token)

    def start_process(
        self, name: str, command: list[str], env: dict[str, str] | None = None
    ) -> subprocess.Popen:
        process = subprocess.Popen(
            command,
            cwd=REPO,
            env=env,
            stdout=(self.tmp / f"{name}.log").open("ab"),
            stderr=subprocess.STDOUT,
        )
        self.processes[name] = process
        return process

    def stop_process(self, name: str) -> None:
        process = self.processes.pop(name, None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def stop(self) -> None:
        for name in list(reversed(self.processes)):
            self.stop_process(name)

    def log_tail(self, lines: int = 80) -> str:
        sections = []
        for path in sorted(self.tmp.glob("*.log")):
            content = path.read_text(errors="replace").splitlines()
            sections.append(f"--- {path.name} ---\n" + "\n".join(content[-lines:]))
        return "\n".join(sections)

    def wait_ready(self, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        last_world: dict = {}
        while time.monotonic() < deadline:
            dead = {
                name: process.returncode
                for name, process in self.processes.items()
                if process.poll() is not None
            }
            if dead:
                raise AssertionError(f"process exited during startup: {dead}\n{self.log_tail()}")
            try:
                devices = self.api("/v1/devices")
                world = self.api("/v1/world")
                last_world = world
                online = {device["robotId"] for device in devices if device.get("online")}
                sources = set(world.get("sources", {}))
                if online == {"robot-1", "robot-2"} and {
                    "robot-1/proprioception",
                    "robot-1/scene",
                    "robot-2/proprioception",
                    "robot-2/scene",
                }.issubset(sources):
                    return
            except (OSError, ValueError, AssertionError):
                pass
            time.sleep(0.25)
        raise AssertionError(
            f"fleet did not become observation-ready; world={last_world}\n{self.log_tail()}"
        )

    def create_and_approve(self, prompt: str = HANDOFF_PROMPT) -> str:
        task = self.api("/v1/tasks", method="POST", body={"request": prompt, "adapter": "mujoco"})
        self.api(f"/v1/tasks/{task['id']}/approve", method="POST")
        return task["id"]

    def wait_task(self, task_id: str, timeout: float = 120) -> dict:
        deadline = time.monotonic() + timeout
        task: dict = {}
        while time.monotonic() < deadline:
            task = self.api(f"/v1/tasks/{task_id}")
            if task.get("state") in {"SUCCEEDED", "FAILED", "FAILED_SAFE", "CANCELLED", "BLOCKED"}:
                return task
            time.sleep(0.25)
        raise AssertionError(f"task did not finish: {task}\n{self.log_tail()}")

    def wait_world(self, predicate, timeout: float = 30) -> dict:
        deadline = time.monotonic() + timeout
        snapshot: dict = {}
        while time.monotonic() < deadline:
            snapshot = self.api("/v1/world")
            if predicate(snapshot):
                return snapshot
            time.sleep(0.25)
        raise AssertionError(f"world predicate timed out: {snapshot}\n{self.log_tail()}")


def _build_binaries(tmp: Path) -> None:
    binary_dir = tmp / "bin"
    binary_dir.mkdir(parents=True)
    environment = dict(os.environ)
    environment["GOCACHE"] = str(tmp / "gocache")
    for name, package in (
        ("fleet-control-plane", "./cmd/fleet-control-plane"),
        ("edge-worker", "./cmd/edge-worker"),
    ):
        subprocess.run(
            ["go", "build", "-o", str(binary_dir / name), package],
            cwd=REPO,
            env=environment,
            check=True,
            timeout=120,
        )


def _generate_certificates(stack: FleetHandoffStack) -> None:
    environment = dict(os.environ)
    environment["FLEET_CERT_DIR"] = str(stack.certs)
    environment["FLEET_ROBOTS"] = "robot-1,robot-2"
    subprocess.run(
        ["bash", "scripts/fleet-certs.sh"], cwd=REPO, env=environment, check=True, timeout=30
    )


def _worker_environment(
    stack: FleetHandoffStack, robot_id: str, runtime_port: int
) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "EDGE_ROBOT_ID": robot_id,
            "EDGE_FLEET_URL": stack.fleet_url,
            "EDGE_DEVICE_TOKEN": f"e2e-device-token-{robot_id.removeprefix('robot-')}",
            "EDGE_RUNTIME_ADDR": f"127.0.0.1:{runtime_port}",
            "EDGE_RUNTIME_INSECURE": "1",
            "EDGE_TASK_SOURCE": "http",
            "EDGE_TELEMETRY_INTERVAL": "250ms",
            "EDGE_HEARTBEAT_INTERVAL": "500ms",
            "EDGE_LEASE": "3s",
            "EDGE_FLEET_GRPC": f"127.0.0.1:{stack.gateway_port}",
            "EDGE_MTLS_CA": str(stack.certs / "fleet-ca.crt"),
            "EDGE_MTLS_CERT": str(stack.certs / f"{robot_id}.crt"),
            "EDGE_MTLS_KEY": str(stack.certs / f"{robot_id}.key"),
            "EDGE_MTLS_SERVER_NAME": "localhost",
            "EDGE_ADAPTER": "mujoco",
            "EDGE_WORLD_ID": "handoff-e2e",
            "EDGE_TRANSFORM_REVISION": "handoff-world-v1",
        }
    )
    return environment


def start_fleet_handoff_stack(tmp_path: Path) -> FleetHandoffStack:
    ports = [free_port() for _ in range(4)]
    while len(set(ports)) != 4:
        ports = [free_port() for _ in range(4)]
    stack = FleetHandoffStack(tmp_path, *ports)
    _build_binaries(tmp_path)
    _generate_certificates(stack)

    fleet_environment = dict(os.environ)
    fleet_environment.update(
        {
            "FLEET_LISTEN": f"127.0.0.1:{stack.fleet_port}",
            "FLEET_GATEWAY_LISTEN": f"127.0.0.1:{stack.gateway_port}",
            "FLEET_STORE": "memory",
            "FLEET_WORLD_ID": "handoff-e2e",
            "FLEET_WORLD_FRESHNESS": "1s",
            "FLEET_HANDOFF_MAX_AGE": "2s",
            "FLEET_DEVICE_LEASE": "3s",
            "FLEET_HEARTBEAT_INTERVAL": "500ms",
            "FLEET_GRPC_REQUIRE_CN": "true",
            "FLEET_GRPC_CA": str(stack.certs / "fleet-ca.crt"),
            "FLEET_GRPC_CERT": str(stack.certs / "fleet-server.crt"),
            "FLEET_GRPC_KEY": str(stack.certs / "fleet-server.key"),
            "FLEET_ROBOTS": "robot-1,robot-2",
            "FLEET_OPERATOR_USER": "admin",
            "FLEET_OPERATOR_PASSWORD": "admin123",
            "FLEET_DEVICE_CREDENTIALS": "robot-1:e2e-device-token-1,robot-2:e2e-device-token-2",
            "FLEET_AUTH_SECRET": "e2e-secret",
        }
    )
    try:
        stack.start_process("fleet", [str(tmp_path / "bin/fleet-control-plane")], fleet_environment)
        _wait_port(stack.fleet_port)
        _wait_port(stack.gateway_port)
        login = api_json(
            stack.fleet_url,
            "/v1/auth/login",
            method="POST",
            body={"user": "admin", "password": "admin123"},
        )
        stack.operator_token = login["token"]

        stack.start_process(
            "sim-fleet",
            [
                str(REPO / ".venv/bin/python"),
                "-m",
                "tangying_sim.fleet_server",
                "--sender-listen",
                f"127.0.0.1:{stack.sim1_port}",
                "--receiver-listen",
                f"127.0.0.1:{stack.sim2_port}",
                "--seed",
                "7",
            ],
        )
        _wait_port(stack.sim1_port)
        _wait_port(stack.sim2_port)
        stack.start_process(
            "edge-robot-1",
            [str(tmp_path / "bin/edge-worker")],
            _worker_environment(stack, "robot-1", stack.sim1_port),
        )
        stack.start_process(
            "edge-robot-2",
            [str(tmp_path / "bin/edge-worker")],
            _worker_environment(stack, "robot-2", stack.sim2_port),
        )
        stack.wait_ready()
        return stack
    except Exception:
        stack.stop()
        raise


@pytest.fixture
def fleet_handoff_stack(tmp_path: Path):
    stack = start_fleet_handoff_stack(tmp_path)
    try:
        yield stack
    finally:
        stack.stop()
