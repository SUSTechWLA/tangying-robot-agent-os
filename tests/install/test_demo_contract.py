from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_demo_is_bounded_to_loopback_and_always_cleans_up():
    script = (ROOT / "scripts/demo.sh").read_text()
    assert "trap cleanup EXIT INT TERM" in script
    assert "127.0.0.1" in script
    assert "mktemp -d" in script
    assert "--dev-insecure" in script
    assert "SUCCEEDED" in script


def test_demo_check_mode_validates_without_starting_stack():
    completed = subprocess.run(
        ["bash", "scripts/demo.sh", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "demo prerequisites: OK" in completed.stdout


def test_demo_runs_only_the_local_agent_and_robot_runtime():
    script = (ROOT / "scripts/demo.sh").read_text()
    assert "./cmd/local-agent" in script
    assert "tangying_sim.server" in script
    assert "cloud-control-plane" not in script
    assert "--cloud" not in script
