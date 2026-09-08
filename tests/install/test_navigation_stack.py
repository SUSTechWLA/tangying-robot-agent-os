from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/navigation-stack.sh"


@pytest.fixture
def navigation_env(tmp_path):
    calls = tmp_path / "calls.jsonl"
    fake = tmp_path / "fake-cli"
    fake.write_text(f"#!{sys.executable}\n" + '''
import hashlib,json,os,sys
from pathlib import Path
root=Path(os.environ["SIM_STACK_ARTIFACTS_DIR"])
root.mkdir(parents=True,exist_ok=True)
state=root/"fake-running"
kind="docker" if "compose" in sys.argv else "sim"
with open(os.environ["FAKE_CALLS"],"a") as stream:
 stream.write(json.dumps({"kind":kind,"args":sys.argv[1:],
  "tokenHash":hashlib.sha256(os.environ.get("TANGYING_NAVIGATION_TOKEN","").encode()).hexdigest(),
  "mode":os.environ.get("TANGYING_NAVIGATION_MODE"),
  "rmw":os.environ.get("RMW_IMPLEMENTATION"),
  "address":os.environ.get("TANGYING_RUNTIME_ADDRESS"),
  "url":os.environ.get("TANGYING_NAVIGATION_URL")})+"\\n")
if kind=="docker":
 if os.environ.get("FAKE_DOCKER_FAIL")=="1":sys.exit(1)
 sys.exit(0)
operation=sys.argv[1]
if operation=="status":sys.exit(0 if state.exists() else 1)
if operation in ("start","restart"):
 if os.environ.get("FAKE_SIM_FAIL")=="1":sys.exit(1)
 state.write_text("running")
 run=root/"run";run.mkdir(exist_ok=True)
 count=root/"generation-counter"
 generation=int(count.read_text())+1 if count.exists() else 1
 count.write_text(str(generation))
 digest="" if os.environ.get("FAKE_DROP_DIGEST")=="1" else os.environ.get("SIM_STACK_NAVIGATION_CONFIG_SHA256","")
 (run/"stack.env").write_text(f"GENERATION=generation-{generation}\\nSIM_PORT=50123\\nAGENT_PORT=18123\\nPERCEPTION=rgbd\\nNAVIGATION_CONFIG_SHA256={digest}\\n")
if operation=="stop":
 state.unlink(missing_ok=True)
 (root/"run/stack.env").unlink(missing_ok=True)
''')
    fake.chmod(0o700)
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
    env.update({"SIM_STACK_ARTIFACTS_DIR": str(tmp_path / "state with spaces"),
                "SIM_STACK_SIM_PORT": "50123", "SIM_STACK_AGENT_PORT": "18123",
                "NAVIGATION_STACK_DOCKER": str(fake), "NAVIGATION_STACK_SIM_SCRIPT": str(fake),
                "NAVIGATION_STACK_PYTHON": sys.executable, "FAKE_CALLS": str(calls)})
    return env, Path(env["SIM_STACK_ARTIFACTS_DIR"]), calls


def run(env, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=REPO, env=env,
                          text=True, capture_output=True, timeout=10, check=False)


def recorded(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_start_reuses_private_token_and_runtime_generation_without_rebuilding(navigation_env):
    env, root, calls = navigation_env
    first = run(env, "start")
    assert first.returncode == 0, first.stderr
    config = root / "navigation.env"
    token = dict(line.split("=", 1) for line in config.read_text().splitlines())["TANGYING_NAVIGATION_TOKEN"]
    assert len(token) >= 32 and stat.S_IMODE(config.stat().st_mode) == 0o600
    assert token not in first.stdout+first.stderr
    second = run(env, "start")
    assert second.returncode == 0, second.stderr
    assert token in config.read_text() and token not in second.stdout+second.stderr
    launches = [call for call in recorded(calls) if call["kind"] == "sim" and call["args"][0] in ("start", "restart")]
    assert len(launches) == 1
    assert launches[0]["args"][1:] == ["--perception", "rgbd"]
    assert launches[0]["tokenHash"] == hashlib.sha256(token.encode()).hexdigest()
    assert launches[0]["url"] == "http://127.0.0.1:18790"
    docker = [call for call in recorded(calls) if call["kind"] == "docker"]
    assert docker and all(call["args"][:3] == ["compose", "-p", "tangying-navigation"] for call in docker)
    assert all("--no-build" in call["args"] for call in docker)


def test_old_runtime_requires_explicit_restart_and_preserves_user_data(navigation_env):
    env, root, calls = navigation_env
    (root / "run").mkdir(parents=True)
    (root / "run/stack.env").write_text("GENERATION=older\nPERCEPTION=rgbd\n")
    (root / "fake-running").write_text("running")
    data = root / "local-agent/tasks.db"
    data.parent.mkdir()
    data.write_bytes(b"retained-task-database")
    start = run(env, "start")
    assert start.returncode != 0 and "restart" in start.stderr
    assert not any(call["kind"] == "docker" for call in recorded(calls))
    restart = run(env, "restart", "--mode", "localization", "--build")
    assert restart.returncode == 0, restart.stderr
    assert data.read_bytes() == b"retained-task-database"
    docker = [call for call in recorded(calls) if call["kind"] == "docker"]
    assert len(docker) == 1 and "--build" in docker[0]["args"] and "--force-recreate" in docker[0]["args"]
    assert docker[0]["mode"] == "localization"


def test_mode_switch_on_start_is_rejected_until_explicit_restart(navigation_env):
    env, root, _calls = navigation_env
    assert run(env, "start").returncode == 0
    before = (root / "navigation.env").read_bytes()
    result = run(env, "start", "--mode", "localization")
    assert result.returncode != 0 and "restart" in result.stderr
    assert (root / "navigation.env").read_bytes() == before
    assert run(env, "restart", "--mode", "localization").returncode == 0
    assert "TANGYING_NAVIGATION_MODE=localization" in (root / "navigation.env").read_text()


def test_rmw_choice_is_persisted_and_changes_require_explicit_restart(navigation_env):
    env, root, calls = navigation_env
    assert run(env, "start").returncode == 0
    assert "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp" in (root / "navigation.env").read_text()
    env["RMW_IMPLEMENTATION"] = "rmw_fastrtps_cpp"
    before = (root / "navigation.env").read_bytes()
    result = run(env, "start")
    assert result.returncode != 0 and "restart" in result.stderr
    assert (root / "navigation.env").read_bytes() == before
    assert run(env, "restart").returncode == 0
    assert "RMW_IMPLEMENTATION=rmw_fastrtps_cpp" in (root / "navigation.env").read_text()
    assert [call for call in recorded(calls) if call["kind"] == "docker"][-1]["rmw"] == "rmw_fastrtps_cpp"


def test_unsupported_rmw_fails_before_launch(navigation_env):
    env, _root, calls = navigation_env
    env["RMW_IMPLEMENTATION"] = "unknown_rmw"
    result = run(env, "start")
    assert result.returncode != 0 and "RMW" in result.stderr
    assert not recorded(calls)


def test_stop_retains_maps_config_and_database_and_targets_only_owned_runtime(navigation_env):
    env, root, calls = navigation_env
    assert run(env, "start").returncode == 0
    result = run(env, "stop")
    assert result.returncode == 0, result.stderr
    assert (root / "navigation.env").exists()
    docker = [call for call in recorded(calls) if call["kind"] == "docker"]
    assert docker[-1]["args"][-1] == "down"
    assert not any(arg in ("-v", "--volumes", "prune") for call in docker for arg in call["args"])
    assert any(call["kind"] == "sim" and call["args"][0] == "stop" for call in recorded(calls))


def test_stop_never_stops_newer_unrelated_runtime_generation(navigation_env):
    env, root, calls = navigation_env
    assert run(env, "start").returncode == 0
    (root / "run/stack.env").write_text("GENERATION=newer-standalone\n")
    result = run(env, "stop")
    assert result.returncode == 0 and (root / "fake-running").exists()
    assert not any(call["kind"] == "sim" and call["args"][0] == "stop" for call in recorded(calls))


def test_saved_config_is_data_never_sourced_as_shell(navigation_env):
    env, root, calls = navigation_env
    root.mkdir(parents=True)
    injected = root / "should-not-exist"
    (root / "navigation.env").write_text(f'TANGYING_NAVIGATION_TOKEN=$(touch "{injected}")\n')
    result = run(env, "start")
    assert result.returncode != 0 and not injected.exists()
    assert not recorded(calls)


def test_runtime_must_attest_config_digest_at_actual_launch(navigation_env):
    env, root, calls = navigation_env
    env["FAKE_DROP_DIGEST"] = "1"
    result = run(env, "start")
    assert result.returncode != 0 and "restart" in result.stderr
    assert not (root / "run/navigation-runtime.json").exists()
    assert not any(call["kind"] == "sim" and call["args"][0] == "stop" for call in recorded(calls))


def test_failed_docker_start_never_restarts_runtime(navigation_env):
    env, root, calls = navigation_env
    env["FAKE_DOCKER_FAIL"] = "1"
    result = run(env, "restart")
    assert result.returncode != 0
    assert not (root / "run/navigation-runtime.json").exists()
    assert not any(call["kind"] == "sim" and call["args"][0] in ("start", "restart", "stop") for call in recorded(calls))


def test_status_requires_real_api_readiness_and_matching_launch_provenance(navigation_env):
    env, root, _calls = navigation_env
    state = {"ready": False, "token": ""}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/v1/navigation/map"
            assert self.headers.get("Authorization") == "Bearer "+state["token"]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"ready": state["ready"], "mode": "mapping"}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env["TANGYING_NAVIGATION_PORT"] = str(server.server_address[1])
        assert run(env, "start").returncode == 0
        state["token"] = dict(line.split("=", 1) for line in (root / "navigation.env").read_text().splitlines())["TANGYING_NAVIGATION_TOKEN"]
        result = run(env, "status")
        assert result.returncode == 1 and '"ready": false' in result.stdout
        state["ready"] = True
        assert run(env, "status").returncode == 0
        (root / "run/stack.env").write_text("GENERATION=different-runtime\n")
        assert run(env, "status").returncode == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("args", [("start", "--mode", "invented"), ("start", "--unknown")])
def test_invalid_options_have_no_side_effects(navigation_env, args):
    env, root, calls = navigation_env
    assert run(env, *args).returncode != 0
    assert not root.exists() and not recorded(calls)
