"""Deterministic distributed-fault matrix.

One scenario crosses live OS process/network boundaries.  The remaining
scenarios target the narrow consistency boundary that owns the invariant;
this keeps failure injection deterministic and avoids shipping an
unauthenticated chaos endpoint in production binaries.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from tests.e2e.fleet_harness import FleetHandoffStack
from tests.e2e.helpers import REPO

pytest_plugins = ("tests.e2e.fleet_harness",)


FAULT_CHECKS = {
    "observation_duplicate_reorder": [
        [
            "go",
            "test",
            "./core/worldmodel",
            "-run",
            "TestProjectorRejectsDuplicateAndOutOfOrderSourceSequence",
            "-count=1",
        ],
    ],
    "worker_crash_after_place": [
        [
            "go",
            "test",
            "./edge/worker",
            "-run",
            "TestWorldNotReadyRetriesCompletionOnly",
            "-count=1",
        ],
        [
            str(REPO / ".venv/bin/pytest"),
            "-q",
            "sim/mujoco/tests/test_server.py::test_service_replays_terminal_event_for_duplicate_command",
        ],
    ],
    "coordinator_restart": [
        [
            "go",
            "test",
            "./fleet/coordinator",
            "-run",
            "TestCoordinatorRestoresRunningIntentWithoutDoubleAdvance",
            "-count=1",
        ],
    ],
    "redis_outage": [
        [
            "go",
            "test",
            "./fleet/coordinator",
            "-run",
            "TestQueueOutageLeavesCommittedHandoffOutboxForRecovery",
            "-count=1",
        ],
    ],
    "stale_fencing_token": [
        [
            str(REPO / ".venv/bin/pytest"),
            "-q",
            "sim/mujoco/tests/test_server.py::test_physical_command_with_stale_fencing_fails_before_dispatch",
        ],
        [
            str(REPO / ".venv/bin/pytest"),
            "-q",
            "sim/mujoco/tests/test_shared_handoff.py::test_stale_handoff_token_cannot_change_shared_block_owner",
        ],
    ],
    "receiver_offline_after_handoff": [
        [
            "go",
            "test",
            "./fleet/coordinator",
            "-run",
            "TestReceiverOfflineAfterHandoffCannotClaimOrDeliver",
            "-count=1",
        ],
        [
            "go",
            "test",
            "./fleet/registry",
            "-run",
            "TestDeviceGoesOfflineAfterLeaseExpiry",
            "-count=1",
        ],
    ],
    "camera_loss_and_ui_reconnect": [
        [
            "go",
            "test",
            "./fleet/coordinator",
            "-run",
            "TestCameraLossDoesNotBlockSemanticGroundTruthHandoff",
            "-count=1",
        ],
        [
            str(REPO / ".venv/bin/pytest"),
            "-q",
            "sim/mujoco/tests/test_server.py::test_renderer_failure_is_nonfatal_and_reported_as_anomaly",
        ],
        ["node", "--test", "web/world_view_test.mjs"],
    ],
    "external_block_move": [
        [
            "go",
            "test",
            "./fleet/coordinator",
            "-run",
            "TestExternalBlockMovePreventsVerifiedCompletion",
            "-count=1",
        ],
    ],
    "versioned_task_update_fencing": [
        [
            "go",
            "test",
            "./fleet/coordinator",
            "-run",
            "TestConfirmRevisionWaitsForRunningIntentThenActivatesAtHarnessSafePoint|TestOlderRevisionAndWrongCommandCannotAdvanceNewGraph|TestCompletionRejectsLowerFenceAndExactDuplicateIsIdempotent",
            "-count=1",
        ],
        [
            "go",
            "test",
            "./tasks",
            "-run",
            "TestMemoryRevisionCommitAllowsExactlyOneConcurrentWriter|TestMemoryRevisionCommitIsIdempotentAndCompareAndSwapProtected",
            "-count=1",
        ],
    ],
    "versioned_task_experience_gap": [
        [
            "node",
            "--test",
            "--test-name-pattern=task experience rejects stale facts and resyncs a skipped revision|late experience response",
            "web/app_test.mjs",
        ],
    ],
}


@pytest.mark.parametrize("fault", sorted(FAULT_CHECKS))
def test_fault_boundary_preserves_consistency_invariant(fault: str):
    for command in FAULT_CHECKS[fault]:
        completed = subprocess.run(
            command,
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert completed.returncode == 0, (
            f"fault={fault} command={command}\n{completed.stdout}\n{completed.stderr}"
        )


def _device_online(stack: FleetHandoffStack, robot_id: str) -> bool:
    devices = stack.api("/v1/devices")
    return any(device["robotId"] == robot_id and device.get("online") for device in devices)


def test_edge_disconnect_reconnect_completes_without_false_advance(
    fleet_handoff_stack: FleetHandoffStack,
):
    stack = fleet_handoff_stack
    edge = stack.processes["edge-robot-1"]
    stack.pause_process("edge-robot-1")
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and _device_online(stack, "robot-1"):
            time.sleep(0.2)
        assert not _device_online(stack, "robot-1"), (
            "robot lease did not expire while edge was disconnected"
        )

        task_id = stack.create_and_approve()
        time.sleep(0.75)
        task = stack.api(f"/v1/tasks/{task_id}")
        assert task["state"] != "SUCCEEDED"
        assert not any(
            event["eventType"] == "BLOCK_AVAILABLE"
            for event in stack.api(f"/v1/tasks/{task_id}/domain-events")
        )

        stack.resume_process("edge-robot-1")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not _device_online(stack, "robot-1"):
            time.sleep(0.2)
        assert _device_online(stack, "robot-1"), stack.log_tail()
        final = stack.wait_task(task_id)
        assert final["state"] == "SUCCEEDED", stack.log_tail()
        events = stack.api(f"/v1/tasks/{task_id}/domain-events")
        assert [event["eventType"] for event in events].count("BLOCK_AVAILABLE") == 1
        assert [event["eventType"] for event in events].count("BLOCK_DELIVERED") == 1
    finally:
        if edge.poll() is None:
            stack.resume_process("edge-robot-1")
