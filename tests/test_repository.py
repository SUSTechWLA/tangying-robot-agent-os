import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_supported_python_runtime():
    assert sys.version_info >= (3, 11)


def test_release_candidate_version_is_consistent():
    assert 'version = "0.1.0rc2"' in (ROOT / "pyproject.toml").read_text()
    for path in (
        ROOT / "sim/mujoco/tangying_sim/server.py",
        ROOT / "robot/ros2_ws/src/tangying_robot_gateway/tangying_ros_gateway/node.py",
    ):
        assert 'software_version="0.1.0-rc.2"' in path.read_text()


def test_ci_covers_fresh_install_plans_and_full_demo():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    for required in (
        "installer-dry-run",
        "macos-14",
        "debian",
        "robot-pi",
        "bash scripts/demo.sh",
        "docker compose",
    ):
        assert required in workflow
