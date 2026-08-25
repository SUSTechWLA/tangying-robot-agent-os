"""RoboCasa fault matrix: recovery or fail-closed at each consistency boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.e2e.helpers import REPO
from tests.e2e.robocasa_harness import start_robocasa_handoff_stack

PYTEST = [sys.executable, "-m", "pytest", "-q"]

FAULT_CHECKS = {
    "observation_duplicate_reorder": [
        ["go", "test", "./core/worldmodel", "-run", "TestProjectorRejectsDuplicateAndOutOfOrderSourceSequence", "-count=1"],
    ],
    "stale_fencing": [
        [
            *PYTEST,
            "sim/robocasa/tests/test_runtime.py::test_runtime_rejects_stale_fencing_after_monotonic_adoption",
        ],
        ["go", "test", "./core/harness", "-run", "TestStaleFencingFailsSafe", "-count=1"],
    ],
    "receiver_offline": [
        ["go", "test", "./fleet/coordinator", "-run", "TestReceiverOfflineAfterHandoffCannotClaimOrDeliver", "-count=1"],
    ],
    "camera_loss_semantics_live": [
        ["go", "test", "./fleet/coordinator", "-run", "TestCameraLossDoesNotBlockSemanticGroundTruthHandoff", "-count=1"],
        [
            *PYTEST,
            "sim/mujoco/tests/test_server.py::test_renderer_failure_is_nonfatal_and_reported_as_anomaly",
        ],
    ],
    "external_block_move": [
        ["go", "test", "./fleet/coordinator", "-run", "TestExternalBlockMovePreventsVerifiedCompletion", "-count=1"],
    ],
    "estop_and_cancel": [
        [
            *PYTEST,
            "sim/mujoco/tests/test_server.py::test_emergency_stop_interrupts_motion_and_recovers_to_safe_pose",
            "sim/mujoco/tests/test_server.py::test_cancel_before_place_commit_keeps_object_held",
        ],
    ],
    "atomic_checkpoint_model_guard": [
        [
            *PYTEST,
            "sim/robocasa/tests/test_checkpoint.py",
        ],
    ],
    "control_plane_restart_catalog_recovery": [
        [
            "go", "test", "./edge/worker",
            "-run", "TestRetryTaskRecoversAfterTransientControlPlaneCatalogLoss",
            "-count=1",
        ],
        [
            "go", "test", "./fleet/gateway",
            "-run", "TestGatewayRejectsLiveAdapterIdentityTakeover",
            "-count=1",
        ],
    ],
    "sensor_capture_identity": [
        [
            "go", "test", "./fleet/telemetry",
            "-run", "TestSensorCaptureOrderingCorrelationAndImmutability|TestSensorCaptureRejectsWrongRobotAndIgnoresDuplicateIdentity",
            "-count=1",
        ],
        [
            "go", "test", "./fleet/redis",
            "-run", "TestSensorCaptureOrderingCorrelationAndTTL",
            "-count=1",
        ],
    ],
    "capture_progress_gate": [
        [
            "go", "test", "./edge/worker",
            "-run", "TestWorkerAttachesAdvancingCaptureRangeToPhysicalTool|TestWorkerLeavesPhysicalToolAwaitingWhenCaptureDoesNotAdvance",
            "-count=1",
        ],
    ],
    "rgb_depth_render_failure": [
        [
            *PYTEST,
            "sim/mujoco/tests/test_rendering.py::test_renderer_discards_failed_backend_and_close_is_idempotent",
            "sim/mujoco/tests/test_server.py::test_renderer_failure_is_nonfatal_and_reported_as_anomaly",
        ],
    ],
    "browser_capture_rejection": [
        [
            "node", "--test",
            "--test-name-pattern=sensor evidence rejects wrong identity|deferred old frame",
            "web/app_test.mjs",
        ],
    ],
    "world_stream_and_multi_dashboard": [
        [
            "node", "--test",
            "--test-name-pattern=revision gap stops rendering|old or duplicate revisions",
            "web/world_view_test.mjs",
        ],
        [
            "node", "--test",
            "--test-name-pattern=Fleet dashboard owns one named polling interval|task polling uses the bounded summary endpoint",
            "web/app_test.mjs",
        ],
        [
            "go", "test", "./fleet",
            "-run", "TestTaskSummaryListIsBoundedAndOmitsHeavyExecutionHistory",
            "-count=1",
        ],
    ],
    "coordinator_restart_and_redis_pause": [
        [
            "go", "test", "./fleet/coordinator",
            "-run", "TestCoordinatorRestoresRunningIntentWithoutDoubleAdvance|TestQueueOutageLeavesCommittedHandoffOutboxForRecovery|TestRenewLeadershipKeepsCoordinatorWritable",
            "-count=1",
        ],
    ],
}


def test_fault_matrix_covers_sensor_browser_and_distributed_consistency_boundaries():
    assert {
        "sensor_capture_identity",
        "capture_progress_gate",
        "rgb_depth_render_failure",
        "browser_capture_rejection",
        "world_stream_and_multi_dashboard",
        "coordinator_restart_and_redis_pause",
    } <= FAULT_CHECKS.keys()


@pytest.mark.parametrize("fault", sorted(FAULT_CHECKS))
def test_fault_boundary_preserves_consistency(fault: str):
    environment = dict(os.environ)
    environment["PYTHONNOUSERSITE"] = "1"
    for command in FAULT_CHECKS[fault]:
        completed = subprocess.run(
            command,
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=environment,
        )
        assert completed.returncode == 0, (
            f"fault={fault} command={command}\n{completed.stdout}\n{completed.stderr}"
        )


def _wait_for(stack, predicate, timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = stack.api("/v1/world")
        if predicate(last):
            return last
        time.sleep(0.25)
    raise AssertionError(f"fault recovery timed out: {last}\n{stack.log_tail()}")


def test_edge_restart_keeps_observation_sequence_monotonic_and_completes(tmp_path: Path):
    stack = start_robocasa_handoff_stack(tmp_path)
    try:
        before = stack.api("/v1/world")["sources"]["robot-1/scene"]["sourceSequence"]
        stack.stop_process("edge-robot-1")
        stack.start_process(
            "edge-robot-1",
            [str(tmp_path / "bin/edge-worker")],
            stack.edge_environments["robot-1"],
        )
        recovered = _wait_for(
            stack,
            lambda world: world.get("sources", {})
            .get("robot-1/scene", {})
            .get("sourceSequence", 0)
            > before,
        )
        assert recovered["sources"]["robot-1/scene"]["freshness"] == "FRESH"
        task_id = stack.create_and_approve()
        assert stack.wait_task(task_id)["state"] == "SUCCEEDED", stack.log_tail()
    finally:
        stack.stop()


def test_runtime_checkpoint_restores_completed_physical_world(tmp_path: Path):
    checkpoint = tmp_path / "robocasa-checkpoint.json"
    stack = start_robocasa_handoff_stack(tmp_path, checkpoint_path=checkpoint)
    try:
        task_id = stack.create_and_approve()
        assert stack.wait_task(task_id)["state"] == "SUCCEEDED", stack.log_tail()
        before = stack.api("/v1/world")["sources"]["robot-1/scene"]["sourceSequence"]
        stack.stop_process("robocasa-runtime")
        assert checkpoint.exists()
        saved = json.loads(checkpoint.read_text())
        assert saved["placement"] == "right-target-zone"
        assert saved["completed"] is True
        stack.start_process(
            "robocasa-runtime", stack.runtime_command, stack.runtime_environment
        )
        recovered = _wait_for(
            stack,
            lambda world: world.get("sources", {})
            .get("robot-1/scene", {})
            .get("sourceSequence", 0)
            > before
            and world.get("entities", {})
            .get("red-block", {})
            .get("relations", {})
            .get("inside")
            == "right-target-zone"
            and world.get("entities", {})
            .get("red-block", {})
            .get("freshness")
            == "FRESH",
            timeout=60,
        )
        assert recovered["entities"]["red-block"]["freshness"] == "FRESH"
    finally:
        stack.stop()
