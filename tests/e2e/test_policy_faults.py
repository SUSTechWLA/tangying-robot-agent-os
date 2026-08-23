"""Policy-tool fault matrix for the simulation-to-real execution boundary."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.e2e.helpers import REPO

POLICY_FAULT_CHECKS: dict[str, list[list[str]]] = {
    "observation_stale_or_degraded": [
        [
            "go",
            "test",
            "./edge/policy",
            "-run",
            "TestObservationValidationRejectsStaleOrDegradedRequiredSources|TestDeterministicProviderStillRejectsStaleObservation",
            "-count=1",
        ]
    ],
    "provider_timeout_or_unavailable": [
        [
            "go",
            "test",
            "./edge/policy",
            "-run",
            "TestHTTPProviderMapsDeadlineWithoutLeakingTransportDetails|TestHTTPProviderBoundsResponsesAndRedactsRemoteBody",
            "-count=1",
        ],
        [
            "go",
            "test",
            "./edge/worker",
            "-run",
            "TestWorkerRetriesPolicyTimeoutBeforeMotionAndEmitsRecovery",
            "-count=1",
        ],
    ],
    "manifest_or_robot_mismatch": [
        [
            "go",
            "test",
            "./edge/policy",
            "-run",
            "TestManifestCompatibilityChecksCapabilityRobotAdapterAndCalibration|TestHTTPProviderRejectsManifestDriftBetweenDiscoveryAndInference",
            "-count=1",
        ]
    ],
    "malformed_or_unsafe_action": [
        [
            "go",
            "test",
            "./edge/policy",
            "-run",
            "TestInferenceResultRejectsUnsafeActions|TestInferenceResultMustEchoIdentityAndManifestRevision",
            "-count=1",
        ],
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "policy/sidecar/tests/test_contracts.py",
            "-k",
            "non_finite_or_out_of_bound",
        ],
    ],
    "unknown_physical_execution": [
        [
            "go",
            "test",
            "./edge/recovery",
            "-run",
            "TestClassifySeparatesSafeRetryFromBlockedAndUnknownExecution",
            "-count=1",
        ]
    ],
    "public_projection_redaction": [
        [
            "go",
            "test",
            "./tasks",
            "-run",
            "TestExperienceExplainsPolicyControlWithoutExposingActionChunk|TestRecoveryGuidanceFromEventsProjectsSafeTimeline",
            "-count=1",
        ],
        ["node", "--test", "web/task_experience_test.mjs"],
    ],
}


@pytest.mark.parametrize("fault", sorted(POLICY_FAULT_CHECKS))
def test_policy_fault_fails_closed_or_recovers_before_motion(fault: str):
    environment = dict(os.environ)
    environment["PYTHONNOUSERSITE"] = "1"
    for command in POLICY_FAULT_CHECKS[fault]:
        completed = subprocess.run(
            command,
            cwd=REPO,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, (
            f"fault={fault} command={command}\n{completed.stdout}\n{completed.stderr}"
        )
