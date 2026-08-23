"""Process harness for the cloud-first two-robot handoff system.

The harness deliberately uses public process boundaries: operator HTTP,
device HTTP, the mTLS Fleet Link, and two Robot Runtime gRPC endpoints.  It
does not reach into coordinator or simulator objects, so the same assertions
remain useful when the runtime endpoints are replaced by physical robots.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib import error, request

import pytest

from tests.e2e.helpers import REPO, free_port

HANDOFF_PROMPT = "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区"


REVISION_FAULT_COMMANDS: dict[str, list[list[str]]] = {
    "concurrent_update": [
        [
            "go",
            "test",
            "./tasks",
            "-run",
            "TestMemoryRevisionCommitAllowsExactlyOneConcurrentWriter",
            "-count=1",
        ]
    ],
    "duplicate_update": [
        [
            "go",
            "test",
            "./tasks",
            "-run",
            "TestMemoryRevisionCommitIsIdempotentAndCompareAndSwapProtected",
            "-count=1",
        ]
    ],
    "delayed_old_completion": [
        [
            "go",
            "test",
            "./fleet/coordinator",
            "-run",
            "TestOlderRevisionAndWrongCommandCannotAdvanceNewGraph",
            "-count=1",
        ]
    ],
    "leader_failover_during_proposal": [
        [
            "go",
            "test",
            "./fleet/coordinator",
            "-run",
            "TestRevisionIdentitySurvivesCoordinatorFailover",
            "-count=1",
        ]
    ],
    "leader_failover_during_confirmation": [
        [
            "go",
            "test",
            "./fleet/coordinator",
            "-run",
            "TestConfirmRevisionWaitsForRunningIntentThenActivatesAtHarnessSafePoint|TestRevisionIdentitySurvivesCoordinatorFailover",
            "-count=1",
        ]
    ],
    "edge_disconnect_running": [
        [
            "go",
            "test",
            "./edge/worker",
            "-run",
            "TestWorkerRefusesClaimFromSupersededTaskRevision|TestWorldNotReadyRetriesCompletionOnly",
            "-count=1",
        ]
    ],
    "tool_timeout": [
        [
            "go",
            "test",
            "./edge/agent",
            "-run",
            "TestRunnerFailsClosedWhenRuntimeReportsNotReady",
            "-count=1",
        ]
    ],
    "tool_ack_without_world_change": [
        [
            "go",
            "test",
            "./core/harness",
            "-run",
            "TestRejectsToolSuccessWithoutPostCommandWorldEvidence",
            "-count=1",
        ]
    ],
    "world_source_stale": [
        [
            "go",
            "test",
            "./fleet/coordinator",
            "-run",
            "TestIntentCompletionWaitsForFreshStableWorldEvidence",
            "-count=1",
        ]
    ],
    "resource_release_delay": [
        [
            "go",
            "test",
            "./fleet/lease",
            "-run",
            "TestExpiredGrantCanBeAcquiredButTokenKeepsIncreasing",
            "-count=1",
        ]
    ],
    "lower_fencing_token": [
        [
            "go",
            "test",
            "./fleet/coordinator",
            "-run",
            "TestCompletionRejectsLowerFenceAndExactDuplicateIsIdempotent",
            "-count=1",
        ],
        ["go", "test", "./core/harness", "-run", "TestStaleFencingFailsSafe", "-count=1"],
    ],
    "experience_refresh_gap": [
        [
            "node",
            "--test",
            "--test-name-pattern=task experience rejects stale facts and resyncs a skipped revision",
            "web/app_test.mjs",
        ]
    ],
}

REVISION_COMMON_COMMANDS = [
    [
        "go",
        "test",
        "./fleet/coordinator",
        "-run",
        "TestOlderRevisionAndWrongCommandCannotAdvanceNewGraph|TestCompletionRejectsLowerFenceAndExactDuplicateIsIdempotent",
        "-count=1",
    ],
    [
        "go",
        "test",
        "./tasks",
        "-run",
        "TestExperienceExplainsRealHandoffStepsWithoutTechnicalIdentifiers|TestExperienceProjectionMatchesPersistedEventReplay|TestMemoryRevisionCommitIsIdempotentAndCompareAndSwapProtected",
        "-count=1",
    ],
    [
        "go",
        "test",
        "./fleet",
        "-run",
        "TestTaskRecoveryGuidanceExplainsDistributedFailuresWithoutRawErrors",
        "-count=1",
    ],
]


@dataclass(frozen=True)
class RevisionFaultResult:
    fault: str
    no_completed_step_rolled_back: bool
    no_old_revision_advanced: bool
    no_duplicate_physical_command: bool
    experience_matches_event_replay: bool
    guidance_is_plain_language: bool
    commands: list[dict]
    summary: str


def run_revision_fault(fault: str) -> RevisionFaultResult:
    """Execute the deterministic owner-boundary checks for one injected fault.

    The command list is retained as evidence so a passing result cannot be
    manufactured without running the consistency owner for that boundary.
    Full process pause/reconnect coverage remains in test_fleet_faults.py.
    """

    if fault not in REVISION_FAULT_COMMANDS:
        raise ValueError(f"unknown revision fault: {fault}")
    records: list[dict] = []
    passed = True
    environment = dict(os.environ)
    environment["PYTHONNOUSERSITE"] = "1"
    for command in [*REVISION_FAULT_COMMANDS[fault], *REVISION_COMMON_COMMANDS]:
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=REPO,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        duration_ms = max(1, round((time.monotonic() - started) * 1000))
        records.append(
            {
                "command": command,
                "exitCode": completed.returncode,
                "durationMs": duration_ms,
                "stdoutTail": completed.stdout[-2000:],
                "stderrTail": completed.stderr[-2000:],
            }
        )
        passed = passed and completed.returncode == 0
    summary = (
        f"安全保持：{fault} 未回滚已确认步骤、未推进旧版本、未重复下发物理命令"
        if passed
        else f"安全检查失败：{fault} 的一致性边界需要处理"
    )
    return RevisionFaultResult(
        fault=fault,
        no_completed_step_rolled_back=passed,
        no_old_revision_advanced=passed,
        no_duplicate_physical_command=passed,
        experience_matches_event_replay=passed,
        guidance_is_plain_language=passed,
        commands=records,
        summary=summary,
    )


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

    def pause_process(self, name: str) -> None:
        process = self.processes.get(name)
        if process is None or process.poll() is not None:
            raise AssertionError(f"cannot pause stopped process {name}")
        process.send_signal(signal.SIGSTOP)

    def resume_process(self, name: str) -> None:
        process = self.processes.get(name)
        if process is None or process.poll() is not None:
            raise AssertionError(f"cannot resume stopped process {name}")
        process.send_signal(signal.SIGCONT)

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

    def experience(self, task_id: str) -> dict:
        return self.api(f"/v1/tasks/{task_id}/experience")

    def revision_history(self, task_id: str) -> dict:
        return self.api(f"/v1/tasks/{task_id}/revisions")

    def propose_update(
        self, task_id: str, expected_revision: int, prompt: str, *, key: str = ""
    ) -> dict:
        return self.api(
            f"/v1/tasks/{task_id}/revisions",
            method="POST",
            body={
                "expectedRevision": expected_revision,
                "request": prompt,
                "idempotencyKey": key or str(uuid.uuid4()),
            },
        )

    def confirm_update(
        self,
        task_id: str,
        revision: int,
        expected_current_revision: int,
        *,
        key: str = "",
    ) -> dict:
        return self.api(
            f"/v1/tasks/{task_id}/revisions/{revision}/confirm",
            method="POST",
            body={
                "expectedCurrentRevision": expected_current_revision,
                "idempotencyKey": key or str(uuid.uuid4()),
            },
        )

    def wait_experience(self, task_id: str, predicate, timeout: float = 30) -> dict:
        deadline = time.monotonic() + timeout
        experience: dict = {}
        while time.monotonic() < deadline:
            experience = self.experience(task_id)
            if predicate(experience):
                return experience
            time.sleep(0.25)
        raise AssertionError(
            f"task experience predicate timed out: {experience}\n{self.log_tail()}"
        )

    def wait_task(self, task_id: str, timeout: float = 120) -> dict:
        deadline = time.monotonic() + timeout
        task: dict = {}
        while time.monotonic() < deadline:
            task = self.api(f"/v1/tasks/{task_id}")
            if task.get("state") in {"SUCCEEDED", "FAILED", "FAILED_SAFE", "CANCELLED", "BLOCKED"}:
                return task
            time.sleep(0.25)
        raise AssertionError(f"task did not finish: {task}\n{self.log_tail()}")

    def wait_task_projection(self, task_id: str, predicate, timeout: float = 15) -> dict:
        """Wait for the eventually delivered worker events behind a task state.

        Coordinator state is authoritative for physical completion, while edge
        activity events are a separate projection consumed by the operator UI.
        """
        deadline = time.monotonic() + timeout
        task: dict = {}
        while time.monotonic() < deadline:
            task = self.api(f"/v1/tasks/{task_id}")
            if predicate(task):
                return task
            time.sleep(0.1)
        raise AssertionError(
            f"task event projection did not catch up: {task}\n{self.log_tail()}"
        )

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
            "EDGE_POLICY_MODE": "deterministic",
            "EDGE_ROBOT_MODEL": "xlerobot-sim",
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
