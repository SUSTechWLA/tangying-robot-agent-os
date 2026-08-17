from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_install(*arguments: str, platform: dict[str, str] | None = None):
    environment = os.environ.copy()
    environment.update(
        {
            "ROBOT_AGENT_TEST_MODE": "1",
            "ROBOT_AGENT_TEST_OS": "linux",
            "ROBOT_AGENT_TEST_DISTRO": "ubuntu",
            "ROBOT_AGENT_TEST_VERSION": "24.04",
            "ROBOT_AGENT_TEST_ARCH": "amd64",
        }
    )
    environment.update(platform or {})
    return subprocess.run(
        ["bash", str(ROOT / "install.sh"), *arguments],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_help_lists_exact_install_roles():
    completed = run_install("--help")
    assert completed.returncode == 0, completed.stderr
    role_lines = {
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip().startswith(("sim ", "cloud ", "local ", "robot-pi "))
    }
    assert role_lines == {
        "sim       complete MuJoCo development stack",
        "cloud     cloud control plane and PostgreSQL",
        "local     laptop Local Agent",
        "robot-pi  Raspberry Pi ROS 2 robot edge",
    }


@pytest.mark.parametrize(
    ("role", "platform"),
    [
        (
            "sim",
            {
                "ROBOT_AGENT_TEST_OS": "darwin",
                "ROBOT_AGENT_TEST_DISTRO": "macos",
                "ROBOT_AGENT_TEST_VERSION": "14",
                "ROBOT_AGENT_TEST_ARCH": "arm64",
            },
        ),
        ("cloud", {}),
        (
            "local",
            {
                "ROBOT_AGENT_TEST_VERSION": "22.04",
                "ROBOT_AGENT_TEST_ARCH": "amd64",
            },
        ),
        (
            "robot-pi",
            {
                "ROBOT_AGENT_TEST_ARCH": "arm64",
            },
        ),
    ],
)
def test_every_role_has_a_non_mutating_dry_run(role: str, platform: dict[str, str]):
    completed = run_install(role, "--dry-run", "--yes", platform=platform)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f"PLAN role={role}" in completed.stdout
    assert "DRY-RUN" in completed.stdout


def test_unsupported_platform_fails_before_mutation_plan():
    completed = run_install(
        "cloud",
        "--dry-run",
        platform={
            "ROBOT_AGENT_TEST_OS": "windows",
            "ROBOT_AGENT_TEST_DISTRO": "windows",
            "ROBOT_AGENT_TEST_VERSION": "11",
        },
    )
    assert completed.returncode != 0
    assert "unsupported platform" in completed.stderr.lower()
    assert "DRY-RUN sudo" not in completed.stdout


def test_test_overrides_are_ignored_without_explicit_test_mode():
    environment = os.environ.copy()
    environment.update(
        {
            "ROBOT_AGENT_TEST_OS": "windows",
            "ROBOT_AGENT_TEST_DISTRO": "windows",
            "ROBOT_AGENT_TEST_VERSION": "11",
            "ROBOT_AGENT_TEST_ARCH": "amd64",
        }
    )
    completed = subprocess.run(
        ["bash", str(ROOT / "install.sh"), "sim", "--dry-run", "--yes"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    expected_os = "darwin" if platform.system() == "Darwin" else "linux"
    assert f"os={expected_os}" in completed.stdout
    assert "os=windows" not in completed.stdout


@pytest.mark.parametrize("role", ["cloud", "local", "robot-pi"])
def test_deployed_roles_install_a_repository_and_lifecycle_cli(role: str):
    platform = {"ROBOT_AGENT_TEST_ARCH": "arm64"} if role == "robot-pi" else {}
    completed = run_install(role, "--dry-run", "--yes", platform=platform)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "install repository checkout" in completed.stdout
    assert "install robot-agent CLI" in completed.stdout


def test_linux_simulation_uses_unprivileged_user_receipt():
    completed = run_install("sim", "--dry-run", "--yes")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "/.local/share/tangying-robot-agent-os/install.json" in completed.stdout
