from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e import robocasa_harness as harness

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOTS = {
    "tangying_robocasa": "sim/robocasa",
    "tangying_sim": "sim/mujoco",
    "tangying_robot_proto": "python",
    "tangying_robot_gateway": "robot/gateway",
}


@pytest.fixture
def stale_runtime(tmp_path: Path, monkeypatch):
    """A real interpreter with an editable .pth pointing at an older checkout."""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    venv = tmp_path / "runtime-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv)],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    python = venv / "bin/python"
    site_packages = Path(
        subprocess.check_output(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            env=environment,
            text=True,
        ).strip()
    )
    old_checkout = tmp_path / "old-worktree"
    for package in PACKAGE_ROOTS:
        source = old_checkout / package
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("", encoding="utf-8")
    (old_checkout / "tangying_robocasa/fleet_server.py").write_text(
        "import importlib.util, json, external_robo_dependency\n"
        f"print(json.dumps({{name: importlib.util.find_spec(name).origin "
        f"for name in {list(PACKAGE_ROOTS)!r}}}))\n",
        encoding="utf-8",
    )
    (site_packages / "old-checkout-editable.pth").write_text(
        str(old_checkout) + "\n", encoding="utf-8"
    )
    dependencies = tmp_path / "external-dependencies"
    dependencies.mkdir()
    (dependencies / "robocasa.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "import tangying_robocasa\n"
        "Path(os.environ['PROBE_ORIGIN_FILE']).write_text(tangying_robocasa.__file__)\n",
        encoding="utf-8",
    )
    (dependencies / "external_robo_dependency.py").write_text("", encoding="utf-8")
    marker = tmp_path / "probe-origin.txt"
    monkeypatch.setenv("ROBOCASA_PYTHON", str(python))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(old_checkout), str(dependencies)]))
    monkeypatch.setenv("PROBE_ORIGIN_FILE", str(marker))
    return python, marker


def test_runtime_probe_imports_current_checkout_despite_stale_editable_install(stale_runtime):
    python, marker = stale_runtime

    assert harness._robocasa_runtime_python() == str(python)
    assert Path(marker.read_text()).resolve() == (
        ROOT / "sim/robocasa/tangying_robocasa/__init__.py"
    ).resolve()


def test_runtime_probe_rejects_interpreter_that_overrides_checkout_paths(stale_runtime):
    _python, marker = stale_runtime
    old_checkout = marker.parent / "old-worktree"
    (marker.parent / "external-dependencies/sitecustomize.py").write_text(
        f"import sys\nsys.path.insert(0, {str(old_checkout)!r})\n", encoding="utf-8"
    )

    with pytest.raises(pytest.skip.Exception, match="different checkout"):
        harness._robocasa_runtime_python()


def test_runtime_launch_imports_current_packages_and_preserves_dependencies(
    tmp_path: Path, monkeypatch, stale_runtime
):
    origins = {}

    def inspect_runtime(_stack, name, command, env):
        if name != "robocasa-runtime":
            return
        completed = subprocess.run(
            [
                command[0],
                "-c",
                (
                    "import importlib.util, json, external_robo_dependency; "
                    f"print(json.dumps({{name: importlib.util.find_spec(name).origin "
                    f"for name in {list(PACKAGE_ROOTS)!r}}}))"
                ),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        origins.update(json.loads(completed.stdout))

    # Keep child import resolution real; replace only unrelated Go/HTTP startup.
    monkeypatch.setattr(harness, "_build_binaries", lambda _tmp: None)
    monkeypatch.setattr(harness, "_generate_certificates", lambda _stack: None)
    monkeypatch.setattr(harness, "_wait_port", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(harness, "api_json", lambda *_args, **_kwargs: {"token": "test"})
    monkeypatch.setattr(harness.RoboCasaHandoffStack, "start_process", inspect_runtime)
    monkeypatch.setattr(harness.RoboCasaHandoffStack, "wait_ready", lambda *_args, **_kwargs: None)

    harness.start_robocasa_handoff_stack(tmp_path, ports=(5101, 5102, 5103, 5104))

    assert origins == {
        package: str(ROOT / source_root / package / "__init__.py")
        for package, source_root in PACKAGE_ROOTS.items()
    }


def test_fleet_script_launches_its_checkout_despite_stale_editable_install(
    tmp_path: Path, stale_runtime
):
    python, _marker = stale_runtime
    checkout = tmp_path / "current-worktree"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "robocasa-fleet.sh"
    shutil.copyfile(ROOT / "scripts/robocasa-fleet.sh", script)
    for package, source_root in PACKAGE_ROOTS.items():
        source = checkout / source_root / package
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("", encoding="utf-8")
    (checkout / "sim/robocasa/tangying_robocasa/fleet_server.py").write_text(
        "import importlib.util, json, external_robo_dependency\n"
        f"print(json.dumps({{name: importlib.util.find_spec(name).origin "
        f"for name in {list(PACKAGE_ROOTS)!r}}}))\n",
        encoding="utf-8",
    )
    certs = checkout / "deploy/cloud/certs"
    certs.mkdir(parents=True)
    (certs / "fleet-ca.crt").touch()
    conda = tmp_path / "conda"
    conda.write_text(
        f"#!/bin/sh\nexec {shlex.quote(str(python))} -c 'import sys; print(sys.executable)'\n",
        encoding="utf-8",
    )
    conda.chmod(0o755)
    environment = os.environ.copy()
    environment["CONDA_BIN"] = str(conda)
    completed = subprocess.run(
        [
            "bash",
            "-c",
            # Source a temporary checkout's script and intercept infrastructure
            # boundaries; runtime Python still executes with the script's env.
            (
                'curl() { return 1; }\nsource "$1" status >/dev/null\n'
                "ensure_cloud() { :; }\nload_env() { :; }\ngo() { :; }\n"
                'launch() { shift 2; env "$@"; }\n'
                "wait_for_port() { :; }\nstart_edge() { :; }\n"
                "device_token_for() { echo test-token; }\nstart\n"
            ),
            "test-fleet-script",
            str(script),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout.splitlines()[0])
    assert report == {
        package: str(checkout / source_root / package / "__init__.py")
        for package, source_root in PACKAGE_ROOTS.items()
    }
