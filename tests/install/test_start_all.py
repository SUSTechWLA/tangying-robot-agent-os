"""Deployment classification and the one-click orchestrator.

The repository ships three deployable targets from one tree. These tests keep the
classification honest: a new top-level area has to be assigned a target in the
documentation, deploy files have to live under the target they belong to, and the
one-click script has to delegate to the lifecycle scripts that own each component
instead of growing a second implementation of them.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Deployment targets documented in docs/deployment.md. `deploy/` holds one
# directory per production target; `config/` is deliberately absent because
# shared example configuration lives with the target that consumes it.
DEPLOY_TARGETS = ("cloud", "robot", "local")

# Top-level trees that are not part of a deployed target: local runtime output,
# downloaded reference checkouts, and vendored dependencies. Everything else has
# to be classified in docs/deployment.md.
NOT_DEPLOYED = {
    "artifacts", "bin", "datasets", "logs", "vendor", "XLeRobot",
    "tangying-ai-operation-system",
}


def tracked_top_level_directories() -> set[str]:
    """Top-level directories only: repository plumbing files are not areas."""
    listed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout.splitlines()
    directories = set()
    for line in listed:
        if "/" not in line:
            continue
        head = line.split("/", 1)[0]
        if not head.startswith("."):
            directories.add(head)
    return directories


def test_every_deploy_target_directory_is_documented():
    deployment = (ROOT / "docs/deployment.md").read_text()
    listed = sorted(
        path.name
        for path in (ROOT / "deploy").iterdir()
        if path.is_dir()
    )
    assert listed == sorted(DEPLOY_TARGETS), (
        f"deploy/ holds {listed}; add the target to docs/deployment.md and this list"
    )
    for target in DEPLOY_TARGETS:
        assert f"deploy/{target}/" in deployment, f"docs/deployment.md does not classify deploy/{target}/"


def test_deploy_directory_has_an_index_and_no_orphan_files():
    index = ROOT / "deploy/README.md"
    assert index.exists(), "deploy/README.md is the map of what is installed where"
    text = index.read_text()
    for target in DEPLOY_TARGETS:
        assert f"{target}/" in text, f"deploy/README.md does not describe {target}/"
    for path in (ROOT / "deploy").iterdir():
        if path.is_file():
            assert path.name == "README.md", f"loose file in deploy/: {path.name}"


def test_every_top_level_source_area_is_classified():
    """A directory counts as classified when the document names it, or names a
    path inside it (cmd/ is covered by cmd/local-agent/ and friends)."""
    deployment = (ROOT / "docs/deployment.md").read_text()

    def classified(directory: str) -> bool:
        if f"`{directory}/`" in deployment:
            return True
        return re.search(rf"`{re.escape(directory)}/[^`]*`", deployment) is not None

    unclassified = sorted(
        directory for directory in tracked_top_level_directories()
        if directory not in NOT_DEPLOYED and not classified(directory)
    )
    assert unclassified == [], (
        "these top-level areas are neither classified in docs/deployment.md nor "
        f"listed as not-deployed: {unclassified}"
    )


def test_robot_and_local_deploy_directories_keep_their_units():
    robot_units = sorted(path.name for path in (ROOT / "deploy/robot/raspberry-pi").iterdir())
    assert "tangying-robot-edge.service" in robot_units
    assert "tangying-robot-edge-direct.service" in robot_units
    assert "tangying-xlerobot.service" in robot_units
    assert "robot-pi.env.example" in robot_units
    local_units = sorted(path.name for path in (ROOT / "deploy/local").iterdir())
    assert "tangying-robot-local-agent.service" in local_units
    assert "local.env.example" in local_units


def test_navigation_compose_context_still_points_at_the_repository_root():
    """The navigation stack moved one level deeper, so `context: ../..` would now
    resolve to deploy/ and the image build would fail to find the ROS workspace."""
    for name in ("compose.yaml", "gazebo-house.compose.yaml"):
        compose = (ROOT / "deploy/robot/navigation" / name).read_text()
        match = re.search(r"^\s*context:\s*(\S+)\s*$", compose, re.MULTILINE)
        assert match, f"{name} has no build context"
        resolved = (ROOT / "deploy/robot/navigation" / match.group(1)).resolve()
        assert resolved == ROOT.resolve(), f"{name} context resolves to {resolved}"
        assert "deploy/robot/navigation/Dockerfile" in compose


def test_start_all_script_delegates_instead_of_reimplementing():
    script = (ROOT / "scripts/start-all.sh").read_text()
    for delegated in ("sim-stack.sh", "navigation-stack.sh", "fleet-up.sh", "fleet-sim.sh", "demo.sh"):
        assert delegated in script, f"start-all.sh does not delegate to {delegated}"
    # It must not grow its own process management.
    for forbidden in ("docker run", "uvicorn", "tangying_sim.server", "nohup"):
        assert forbidden not in script, f"start-all.sh reimplements lifecycle work: {forbidden}"


def test_start_all_check_starts_nothing_and_reports_the_plan():
    completed = subprocess.run(
        ["bash", "scripts/start-all.sh", "check"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "prerequisites: OK" in completed.stdout
    assert "components: sim" in completed.stdout


def test_start_all_refuses_fleet_edges_without_a_cloud():
    completed = subprocess.run(
        ["bash", "scripts/start-all.sh", "check", "--with-fleet-sim"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert completed.returncode != 0
    assert "--with-cloud" in completed.stdout + completed.stderr


def test_start_all_down_without_recorded_state_changes_nothing():
    state = ROOT / "artifacts/start-all/state"
    backup = state.read_text() if state.exists() else None
    state.unlink(missing_ok=True)
    try:
        completed = subprocess.run(
            ["bash", "scripts/start-all.sh", "down"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "nothing recorded" in completed.stdout
    finally:
        if backup is not None:
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(backup)


@pytest.mark.parametrize("target", DEPLOY_TARGETS)
def test_documented_target_has_an_entry_command(target: str):
    deployment = (ROOT / "docs/deployment.md").read_text()
    section = deployment.split(f"deploy/{target}/", 1)[1]
    assert ("install.sh" in section) or ("fleet-up.sh" in section), (
        f"docs/deployment.md does not name how {target} is started"
    )
