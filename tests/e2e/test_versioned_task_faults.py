"""Fail-closed matrix for task updates crossing distributed boundaries."""

from __future__ import annotations

import pytest

from tests.e2e.fleet_harness import run_revision_fault

REVISION_FAULTS = (
    "concurrent_update",
    "duplicate_update",
    "delayed_old_completion",
    "leader_failover_during_proposal",
    "leader_failover_during_confirmation",
    "edge_disconnect_running",
    "tool_timeout",
    "tool_ack_without_world_change",
    "world_source_stale",
    "resource_release_delay",
    "lower_fencing_token",
    "experience_refresh_gap",
)


@pytest.mark.parametrize("fault", REVISION_FAULTS)
def test_versioned_update_faults_fail_closed(fault: str):
    result = run_revision_fault(fault)

    assert result.fault == fault
    assert result.no_completed_step_rolled_back
    assert result.no_old_revision_advanced
    assert result.no_duplicate_physical_command
    assert result.experience_matches_event_replay
    assert result.guidance_is_plain_language
    assert result.commands, "the result must retain the exact verification commands"
    assert all(command["exitCode"] == 0 for command in result.commands), result.commands
    assert all(command["durationMs"] > 0 for command in result.commands)
    assert result.summary.startswith("安全"), result.summary


def test_unknown_revision_fault_is_rejected():
    with pytest.raises(ValueError, match="unknown revision fault"):
        run_revision_fault("invented_fault")
