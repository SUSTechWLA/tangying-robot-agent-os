"""Public-boundary process harness for RoboCasa + two XLeRobot edges."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
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


def _source_pythonpath(environment: dict[str, str]) -> str:
    source_roots = (
        REPO / "python",
        REPO / "sim" / "mujoco",
        REPO / "sim" / "robocasa",
        REPO / "robot" / "gateway",
        REPO / "policy" / "sidecar",
    )
    entries = [str(path) for path in source_roots]
    existing = environment.get("PYTHONPATH", "")
    if existing:
        entries.append(existing)
    return os.pathsep.join(entries)


@dataclass(frozen=True)
class RoboCasaHandoffRun:
    task_id: str
    task: dict[str, object]
    initial_world: dict[str, object]
    final_world: dict[str, object]
    initial_capture: dict[str, object]
    world_trajectory: list[dict[str, object]]
    sensor_trajectory: list[dict[str, object]]
    tool_activities: list[dict[str, object]]

    @property
    def tool_names(self) -> list[str]:
        names: list[str] = []
        for activity in self.tool_activities:
            payload = activity.get("payload", {})
            if not isinstance(payload, dict) or payload.get("activityStatus") != "SENDING":
                continue
            name = str(payload.get("toolName", ""))
            if name:
                names.append(name)
        return names

    def _synchronizations_for(self, *, robot_id: str = "", tool_name: str = "") -> list[dict]:
        synchronizations: list[dict] = []
        for activity in self.tool_activities:
            payload = activity.get("payload", {})
            if not isinstance(payload, dict):
                continue
            if robot_id and payload.get("robotId") != robot_id:
                continue
            if tool_name and payload.get("toolName") != tool_name:
                continue
            synchronization = payload.get("synchronization")
            if isinstance(synchronization, dict):
                synchronizations.append(synchronization)
        return synchronizations

    def rgbd_advanced_for(self, robot_id: str) -> bool:
        capture_ids: set[str] = set()
        for synchronization in self._synchronizations_for(robot_id=robot_id):
            capture_ids.update(
                str(value)
                for value in (
                    synchronization.get("basisCaptureId"),
                    synchronization.get("latestCaptureId"),
                )
                if value
            )
        captures = [
            sample
            for sample in self.sensor_trajectory
            if sample.get("robotId") == robot_id
            and sample.get("episodeId") == self.initial_capture.get("episodeId")
        ]
        modalities_valid = all(
            {frame.get("modality") for frame in sample.get("frames", [])} == {"rgb", "depth"}
            and all(frame.get("sha256") and frame.get("width") and frame.get("height") for frame in sample.get("frames", []))
            for sample in captures
        )
        return len(capture_ids) >= 2 and bool(captures) and modalities_valid

    def world_changed_while(self, tool_name: str) -> bool:
        for synchronization in self._synchronizations_for(tool_name=tool_name):
            basis_capture = synchronization.get("basisCaptureId")
            latest_capture = synchronization.get("latestCaptureId")
            basis_revision = int(synchronization.get("basisWorldRevision") or 0)
            latest_revision = int(synchronization.get("latestWorldRevision") or 0)
            if (basis_capture and latest_capture and basis_capture != latest_capture) or latest_revision > basis_revision:
                return True
        return False


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

    def create_and_approve(self, prompt: str = HANDOFF_PROMPT, *, new_episode: bool = False) -> str:
        body: dict[str, object] = {"request": prompt, "adapter": "robocasa"}
        if new_episode:
            body["executionContext"] = {"mode": "simulation_demo", "newEpisode": True}
        task = self.api(
            "/v1/tasks",
            method="POST",
            body=body,
        )
        self.api(f"/v1/tasks/{task['id']}/approve", method="POST")
        return task["id"]

    def latest_capture(self, robot_id: str) -> dict[str, object]:
        return self.api(f"/v1/sensors/latest/{robot_id}")

    def wait_latest_capture(
        self,
        robot_id: str,
        *,
        timeout: float = 15,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        last_error: AssertionError | None = None
        while time.monotonic() < deadline:
            try:
                return self.latest_capture(robot_id)
            except AssertionError as exc:
                if "SENSOR_CAPTURE_UNAVAILABLE" not in str(exc):
                    raise
                last_error = exc
                time.sleep(0.05)
        raise AssertionError(
            f"{robot_id} did not publish an initial synchronized RGB-D capture"
        ) from last_error

    def wait_capture_after(
        self,
        robot_id: str,
        capture_id: str,
        *,
        timeout: float = 10,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            capture = self.latest_capture(robot_id)
            if capture.get("captureId") != capture_id:
                return capture
            time.sleep(0.05)
        raise AssertionError(f"{robot_id} RGB-D capture stayed frozen after task completion")

    def optional_latest_capture(self, robot_id: str) -> dict[str, object]:
        try:
            return self.latest_capture(robot_id)
        except AssertionError as exc:
            if "SENSOR_CAPTURE_UNAVAILABLE" not in str(exc):
                raise
            return {}

    def run_handoff(
        self,
        prompt: str = HANDOFF_PROMPT,
        *,
        new_episode: bool = False,
        timeout: float = 120,
    ) -> RoboCasaHandoffRun:
        before_world = self.api("/v1/world")
        before_world_revision = int(before_world.get("revision") or 0)
        before_captures = {
            robot_id: self.optional_latest_capture(robot_id)
            for robot_id in ("robot-1", "robot-2")
        }
        task_id = self.create_and_approve(prompt, new_episode=new_episode)
        deadline = time.monotonic() + timeout
        world_trajectory: list[dict[str, object]] = []
        sensor_trajectory: list[dict[str, object]] = []
        world_revisions: set[int] = set()
        capture_ids: set[tuple[str, str]] = set()
        initial_world: dict[str, object] | None = None
        initial_capture: dict[str, object] | None = None
        task: dict[str, object] = {}

        while time.monotonic() < deadline:
            world = self.api("/v1/world")
            revision = int(world.get("revision") or 0)
            if revision not in world_revisions:
                world_revisions.add(revision)
                world_trajectory.append(world)

            for robot_id in ("robot-1", "robot-2"):
                capture = self.optional_latest_capture(robot_id)
                if not capture:
                    continue
                identity = (robot_id, str(capture.get("captureId", "")))
                if identity not in capture_ids:
                    capture_ids.add(identity)
                    sensor_trajectory.append(capture)
                if (
                    robot_id == "robot-1"
                    and capture.get("episodeId")
                    and (
                        not new_episode
                        or capture.get("episodeId") != before_captures[robot_id].get("episodeId")
                    )
                    and initial_capture is None
                ):
                    initial_capture = capture

            placement = (
                world.get("entities", {})
                .get("red-block", {})
                .get("relations", {})
                .get("inside")
            )
            # World projection and head-camera publication are independent
            # streams. On a fast deterministic run the reset world can arrive
            # one poll before the first capture from the new episode. Preserve
            # both facts independently, then require both before returning.
            if (
                revision > before_world_revision
                and placement == "left-start-zone"
                and initial_world is None
            ):
                initial_world = world

            task = self.api(f"/v1/tasks/{task_id}")
            if task.get("state") in {"SUCCEEDED", "FAILED", "FAILED_SAFE", "CANCELLED", "BLOCKED"}:
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"task did not finish: {task}\n{self.log_tail()}")

        task = self.wait_task_projection(
            task_id,
            lambda projected: any(
                event.get("type") == "TOOL_ACTIVITY"
                and event.get("payload", {}).get("activityStatus") == "CONFIRMED"
                for event in projected.get("events", [])
            ),
        )
        final_world = self.api("/v1/world")
        if int(final_world.get("revision") or 0) not in world_revisions:
            world_trajectory.append(final_world)
        for robot_id in ("robot-1", "robot-2"):
            capture = self.latest_capture(robot_id)
            identity = (robot_id, str(capture.get("captureId", "")))
            if identity not in capture_ids:
                sensor_trajectory.append(capture)

        tool_activities = [
            event for event in task.get("events", []) if event.get("type") == "TOOL_ACTIVITY"
        ]
        if initial_capture is None or initial_world is None:
            raise AssertionError(
                "new episode reset was not observed before manipulation completed\n"
                f"world trajectory={world_trajectory}\nsensor trajectory={sensor_trajectory}\n{self.log_tail()}"
            )
        return RoboCasaHandoffRun(
            task_id=task_id,
            task=task,
            initial_world=initial_world,
            final_world=final_world,
            initial_capture=initial_capture,
            world_trajectory=world_trajectory,
            sensor_trajectory=sensor_trajectory,
            tool_activities=tool_activities,
        )

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
            "EDGE_POLICY_MODE": "deterministic",
            "EDGE_ROBOT_MODEL": "xlerobot-sim",
            "EDGE_WORLD_ID": "robocasa-handoff-v1",
            "EDGE_TRANSFORM_REVISION": "robocasa-world-v1",
        }
    )
    return environment


def _robocasa_runtime_python() -> str:
    """Resolve only an interpreter that proves the optional runtime is usable."""

    candidate = os.environ.get("ROBOCASA_PYTHON", sys.executable)
    environment = dict(os.environ)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONPATH"] = _source_pythonpath(environment)
    try:
        probe = subprocess.run(
            [candidate, "-c", "import robocasa, tangying_robocasa"],
            cwd=REPO,
            env=environment,
            capture_output=True,
            text=True,
            # RoboCasa imports MuJoCo and its model stack. On a loaded CI host
            # (especially while Go/Python suites run in parallel) a healthy
            # interpreter can take more than ten seconds to import.
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"RoboCasa runtime interpreter is unavailable: {exc}")
    if probe.returncode != 0:
        pytest.skip(
            "RoboCasa runtime is not installed in the active interpreter; "
            "run `make test-robocasa` or set ROBOCASA_PYTHON"
        )
    return candidate


def start_robocasa_handoff_stack(
    tmp_path: Path,
    *,
    checkpoint_path: Path | None = None,
    human_speed: float = 0.0,
    ports: tuple[int, int, int, int] | None = None,
    episode_nonce: str = "",
) -> RoboCasaHandoffStack:
    runtime_python = _robocasa_runtime_python()
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
    runtime_environment["PYTHONPATH"] = _source_pythonpath(runtime_environment)
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
