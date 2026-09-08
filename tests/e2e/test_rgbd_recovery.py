"""Real HTTP/gRPC/SQLite recovery checks against a camera-driven simulator."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rgbd_agent_binary(tmp_path_factory):
    binary = tmp_path_factory.mktemp("rgbd-agent") / "local-agent"
    subprocess.run(
        ["go", "build", "-o", str(binary), "./cmd/local-agent"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=90,
    )
    return binary


@pytest.mark.parametrize("scenario", ["pause-restart", "unknown-outcome"])
def test_rgbd_task_recovery_uses_durable_receipts_and_camera_evidence(
    tmp_path,
    rgbd_agent_binary,
    scenario,
):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_rgbd_acceptance.py",
            "--agent",
            str(rgbd_agent_binary),
            "--scenario",
            scenario,
            "--output",
            str(tmp_path / "evidence"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
