from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_ros_ci_installs_only_declared_transport_pins_and_exposes_them_to_colcon(
    monkeypatch, tmp_path,
):
    steps = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["ros-build"]["steps"]
    setup = next(step for step in steps if step.get("name") == "Install shared protocol dependencies for ROS tests")
    assert "venv --system-site-packages" in setup["run"]
    program = re.search(r"<<'PY'\n(.*?)\nPY", setup["run"], re.DOTALL)
    assert program is not None
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    monkeypatch.chdir(ROOT)
    environment = tmp_path / "github-env"
    monkeypatch.setenv("GITHUB_ENV", str(environment))
    # Exercise our checked-in CI program; pip is replaced with the recorder above.
    exec(compile(program.group(1), "ros-ci-protocol-install", "exec"), {})  # noqa: S102
    declarations = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    expected = [next(dep for dep in declarations if dep.startswith(name + "=="))
                for name in ("grpcio", "protobuf")]
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[1:] == ["-m", "pip", "install", *expected]
    assert kwargs == {"check": True}
    assert environment.read_text().startswith("ROS_PROTOCOL_SITE_PACKAGES=")
    test = next(step["run"] for step in steps if "colcon test --packages-select" in step.get("run", ""))
    assert test.index(". install/setup.sh") < test.index("export PYTHONPATH=") < test.index("colcon test ")
    assert "$ROS_PROTOCOL_SITE_PACKAGES:$GITHUB_WORKSPACE/python" in test
    assert 'PYTHONPATH:+:$PYTHONPATH' in test
    assert "import rclpy" in test
    assert "robot_pb2_grpc" in test
