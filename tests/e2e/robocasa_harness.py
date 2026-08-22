"""Public-boundary process harness for RoboCasa + two XLeRobot edges."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib import request
from urllib.parse import urljoin, urlsplit

import pytest

from tests.e2e.fleet_harness import (
    HANDOFF_PROMPT,
    FleetHandoffStack,
    _build_binaries,
    _generate_certificates,
    _wait_port,
    api_json,
)
from tests.e2e.helpers import REPO, free_port


class RoboCasaHandoffStack(FleetHandoffStack):
    @property
    def base_url(self) -> str:
        return self.fleet_url

    def public_bytes(self, path_or_url: str, *, base_url: str | None = None) -> bytes:
        expected_base = (base_url or self.base_url).rstrip("/")
        url = _same_origin_public_url(expected_base, path_or_url)
        opener = request.build_opener(_RejectRedirects())
        with opener.open(url, timeout=10) as response:
            _assert_same_origin(expected_base, response.geturl())
            return response.read()

    def public_json(self, path: str) -> dict[str, object]:
        return json.loads(self.public_bytes(path))

    def create_and_approve(self, prompt: str = HANDOFF_PROMPT) -> str:
        task = self.api(
            "/v1/tasks",
            method="POST",
            body={"request": prompt, "adapter": "robocasa"},
        )
        self.api(f"/v1/tasks/{task['id']}/approve", method="POST")
        return task["id"]

    def wait_experience_step(
        self,
        task_id: str,
        *,
        step_index: int,
        status: str,
        timeout: float = 30,
    ) -> dict[str, object]:
        """Wait for a user-visible step state without reading runtime internals."""

        deadline = time.monotonic() + timeout
        experience: dict[str, object] = {}
        while time.monotonic() < deadline:
            experience = self.experience(task_id)
            steps = experience.get("steps", [])
            if (
                isinstance(steps, list)
                and len(steps) > step_index
                and isinstance(steps[step_index], dict)
                and steps[step_index].get("status") == status
            ):
                return experience
            time.sleep(0.01)
        raise AssertionError(
            f"task step {step_index} did not reach {status}: {experience}\n{self.log_tail()}"
        )


def _origin(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.netloc


def _assert_same_origin(base_url: str, url: str) -> None:
    if _origin(url) != _origin(base_url):
        raise AssertionError(f"public asset origin mismatch: {url}")


def _same_origin_public_url(base_url: str, path_or_url: str) -> str:
    url = urljoin(base_url.rstrip("/") + "/", path_or_url)
    _assert_same_origin(base_url, url)
    return url


class _RejectRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AssertionError(f"public asset redirect rejected: {newurl}")


def _robocasa_worker_environment(
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
            "EDGE_ADAPTER": "robocasa",
            "EDGE_WORLD_ID": "robocasa-handoff-v1",
            "EDGE_TRANSFORM_REVISION": "robocasa-world-v1",
        }
    )
    return environment


def start_robocasa_handoff_stack(
    tmp_path: Path,
    *,
    checkpoint_path: Path | None = None,
    human_speed: float = 0.0,
    ports: tuple[int, int, int, int] | None = None,
    episode_nonce: str = "",
) -> RoboCasaHandoffStack:
    selected_ports = list(ports) if ports is not None else [free_port() for _ in range(4)]
    while ports is None and len(set(selected_ports)) != 4:
        selected_ports = [free_port() for _ in range(4)]
    if len(selected_ports) != 4 or len(set(selected_ports)) != 4:
        raise AssertionError("RoboCasa stack requires four distinct ports")
    stack = RoboCasaHandoffStack(tmp_path, *selected_ports)
    _build_binaries(tmp_path)
    _generate_certificates(stack)

    fleet_environment = dict(os.environ)
    fleet_environment.update(
        {
            "FLEET_LISTEN": f"127.0.0.1:{stack.fleet_port}",
            "FLEET_GATEWAY_LISTEN": f"127.0.0.1:{stack.gateway_port}",
            "FLEET_STORE": "memory",
            "FLEET_WORLD_ID": "robocasa-handoff-v1",
            "FLEET_WORLD_FRESHNESS": "10m" if episode_nonce else "1s",
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
            "FLEET_AUTH_SECRET": "robocasa-e2e-secret",
            "FLEET_ACCEPTANCE_NONCE": episode_nonce,
        }
    )
    conda = shutil.which("conda")
    if conda is None:
        raise AssertionError("conda is required for the RoboCasa process harness")
    environment_name = os.environ.get("ROBOCASA_ENV_NAME", "tangying-robocasa")
    runtime_python = (
        subprocess.check_output(
            [
                conda,
                "run",
                "-n",
                environment_name,
                "python",
                "-c",
                "import sys; print(sys.executable)",
            ],
            cwd=REPO,
            text=True,
            timeout=30,
        )
        .strip()
        .splitlines()[-1]
    )
    runtime_command = [
        runtime_python,
        "-m",
        "tangying_robocasa.fleet_server",
        "--sender-listen",
        f"127.0.0.1:{stack.sim1_port}",
        "--receiver-listen",
        f"127.0.0.1:{stack.sim2_port}",
        "--seed",
        "7",
        "--human-speed",
        str(human_speed),
    ]
    if checkpoint_path is not None:
        runtime_command.extend(["--checkpoint", str(checkpoint_path)])
    runtime_environment = dict(os.environ)
    runtime_environment["PYTHONNOUSERSITE"] = "1"
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
        stack.start_process("robocasa-runtime", runtime_command, runtime_environment)
        _wait_port(stack.sim1_port, timeout=60)
        _wait_port(stack.sim2_port, timeout=60)
        edge_environments = {
            robot_id: _robocasa_worker_environment(stack, robot_id, port)
            for robot_id, port in (
                ("robot-1", stack.sim1_port),
                ("robot-2", stack.sim2_port),
            )
        }
        for robot_id in ("robot-1", "robot-2"):
            stack.start_process(
                f"edge-{robot_id}",
                [str(tmp_path / "bin/edge-worker")],
                edge_environments[robot_id],
            )
        stack.runtime_command = runtime_command
        stack.runtime_environment = runtime_environment
        stack.edge_environments = edge_environments
        stack.wait_ready(timeout=90)
        return stack
    except Exception:
        stack.stop()
        raise


@pytest.fixture
def robocasa_stack(tmp_path: Path):
    stack = start_robocasa_handoff_stack(tmp_path)
    try:
        yield stack
    finally:
        stack.stop()
