from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_pair(tmp_path: Path):
    state = tmp_path / "laptop-state"
    config = tmp_path / "laptop-config"
    remote = tmp_path / "robot-root"
    environment = os.environ.copy()
    environment.update(
        {
            "ROBOT_AGENT_TEST_MODE": "1",
            "ROBOT_AGENT_PAIR_LOCAL_ROOT": str(remote),
            "ROBOT_AGENT_PAIR_IP": "192.168.50.73",
            "ROBOT_AGENT_STATE_DIR": str(state),
            "ROBOT_AGENT_CONFIG_DIR": str(config),
        }
    )
    completed = subprocess.run(
        ["bash", "scripts/pair-robot.sh", "xlerobot.local", "--ssh-user", "tangying-robot"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed, state, config, remote


def test_pairing_keeps_ca_private_key_on_laptop_and_deploys_only_edge_material(tmp_path):
    completed, state, _, remote = run_pair(tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    local_certs = state / "certs"
    remote_certs = remote / "var/lib/tangying-robot-agent-os/certs"
    assert (local_certs / "ca.key").exists()
    assert (local_certs / "ca.crt").exists()
    assert (local_certs / "local-agent.key").exists()
    assert (local_certs / "local-agent.crt").exists()
    assert (remote_certs / "server.key").exists()
    assert (remote_certs / "server.crt").exists()
    assert (remote_certs / "client-ca.crt").exists()
    assert not (remote_certs / "ca.key").exists()
    assert stat.S_IMODE((local_certs / "ca.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((remote_certs / "server.key").stat().st_mode) == 0o600


def test_server_certificate_contains_robot_dns_and_ip_sans(tmp_path):
    completed, _, _, remote = run_pair(tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    certificate = remote / "var/lib/tangying-robot-agent-os/certs/server.crt"
    inspected = subprocess.run(
        ["openssl", "x509", "-in", str(certificate), "-noout", "-text"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert "DNS:xlerobot.local" in inspected
    assert "IP Address:192.168.50.73" in inspected


def test_repairing_rotates_leaf_certificates_but_preserves_ca(tmp_path):
    first, state, _, _ = run_pair(tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    ca_key = state / "certs/ca.key"
    before = hashlib.sha256(ca_key.read_bytes()).hexdigest()
    second, _, _, _ = run_pair(tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    after = hashlib.sha256(ca_key.read_bytes()).hexdigest()
    assert before == after


def test_pairing_updates_local_agent_certificate_configuration(tmp_path):
    completed, state, config, _ = run_pair(tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    local_config = (config / "local.env").read_text()
    assert "ROBOT_ADDRESS=xlerobot.local:50051" in local_config
    assert "ROBOT_SERVER_NAME=xlerobot.local" in local_config
    assert f"ROBOT_CA={state}/certs/ca.crt" in local_config
    assert f"ROBOT_CERT={state}/certs/local-agent.crt" in local_config
    assert f"ROBOT_KEY={state}/certs/local-agent.key" in local_config

