from __future__ import annotations

import hashlib
import os
import socket
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



def test_pairing_enables_the_robot_service_so_it_survives_a_power_cycle(tmp_path):
    """Enabling happens here, not at install time.

    Enabled at install, an unpaired robot's edge service would start at every boot,
    fail on the missing certificate and be restarted until systemd gave up — a
    flapping unit nobody is told about. This is the moment the robot can serve, so
    it is the moment to make it permanent.
    """
    completed, _, _, remote = run_pair(tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    command = (remote / "pair-command.log").read_text()
    assert "systemctl enable tangying-robot-edge.service" in command
    # Restarting alone proved nothing: `try-restart` is a no-op on a stopped unit.
    assert "systemctl try-restart tangying-robot-edge.service" in command
    assert command.index("enable tangying-robot-edge.service") < command.index("try-restart")


def free_port() -> int:
    """A port nothing is listening on, obtained from the kernel.

    Hardcoding 50051 made this test depend on the machine: on a developer machine
    running the simulator that port is in use, and the "connection refused" case
    became a success.
    """
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_pairing_refuses_to_report_success_when_the_robot_does_not_answer(tmp_path):
    """A pairing that cannot be verified is not a completed pairing.

    It used to end at "pairing complete" having proved only that a name resolved,
    so a blocked port, a firewall rule and a service that failed to start all
    produced the same cheerful message.
    """
    state = tmp_path / "laptop-state"
    config = tmp_path / "laptop-config"
    remote = tmp_path / "robot-root"
    environment = os.environ.copy()
    environment.update(
        {
            "ROBOT_AGENT_TEST_MODE": "1",
            "ROBOT_AGENT_PAIR_LOCAL_ROOT": str(remote),
            "ROBOT_AGENT_PAIR_IP": "127.0.0.1",
            "ROBOT_AGENT_STATE_DIR": str(state),
            "ROBOT_AGENT_CONFIG_DIR": str(config),
            "ROBOT_AGENT_PAIR_PORT": str(free_port()),
            "ROBOT_AGENT_PAIR_VERIFY": "1",
            "ROBOT_AGENT_PAIR_VERIFY_ATTEMPTS": "1",
        }
    )
    completed = subprocess.run(
        ["bash", "scripts/pair-robot.sh", "127.0.0.1", "--ssh-user", "tangying-robot"],
        cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
    )
    # The material is deployed and the failure is reported: the operator is told
    # what to check rather than told everything is fine.
    assert (state / "certs" / "local-agent.crt").exists()
    assert completed.returncode != 0, completed.stdout
    assert "did not accept a connection" in completed.stderr
    assert "systemctl status tangying-robot-edge.service" in completed.stderr


def test_pairing_verification_can_be_skipped_explicitly(tmp_path):
    """An operator on a network that blocks the probe must be able to say so.

    The skip is announced rather than silent: an unverified pairing and a verified
    one must not look the same in the output.
    """
    state = tmp_path / "laptop-state"
    remote = tmp_path / "robot-root"
    environment = os.environ.copy()
    environment.update(
        {
            "ROBOT_AGENT_TEST_MODE": "1",
            "ROBOT_AGENT_PAIR_LOCAL_ROOT": str(remote),
            "ROBOT_AGENT_PAIR_IP": "192.168.50.73",
            "ROBOT_AGENT_STATE_DIR": str(state),
            "ROBOT_AGENT_CONFIG_DIR": str(tmp_path / "laptop-config"),
            "ROBOT_AGENT_PAIR_VERIFY": "0",
        }
    )
    completed = subprocess.run(
        ["bash", "scripts/pair-robot.sh", "xlerobot.local", "--ssh-user", "tangying-robot"],
        cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "reachability check skipped" in completed.stderr
    # And the completion message says so, so an unverified pairing is never read
    # as a verified one.
    assert "unverified" in completed.stdout
