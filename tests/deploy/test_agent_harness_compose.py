"""The same agent image must have distinct and isolated deployment roles."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_cloud_and_orin_use_one_multi_arch_agent_image():
    cloud = yaml.safe_load((ROOT / "deploy/cloud/docker-compose.yml").read_text())
    edge = yaml.safe_load((ROOT / "deploy/edge-orin/compose.yaml").read_text())
    cloud_agent = cloud["services"]["fleet-control-plane"]
    local_agent = edge["services"]["local-agent"]
    worker = edge["services"]["edge-worker"]
    assert {cloud_agent["build"]["dockerfile"], local_agent["build"]["dockerfile"], worker["build"]["dockerfile"]} == {"Dockerfile.agent"}
    assert cloud_agent["command"] == ["server"]
    assert cloud_agent["user"].startswith("65534:")
    assert set(cloud_agent["volumes"]) == {
        "./certs/fleet-ca.crt:/certs/fleet-ca.crt:ro",
        "./certs/fleet-server.crt:/certs/fleet-server.crt:ro",
        "./certs/fleet-server.key:/certs/fleet-server.key:ro",
        "fleet-world:/var/lib/tangying-fleet",
    }
    assert local_agent["command"] == ["edge"]
    assert worker["command"] == ["worker"]
    assert local_agent["profiles"] == ["edge"]
    assert worker["profiles"] == ["fleet"]
    assert local_agent["network_mode"] == worker["network_mode"] == "host"
    assert local_agent["environment"]["ROBOT_CONTROL_LOCK_DIR"] == worker["environment"]["ROBOT_CONTROL_LOCK_DIR"]
    assert local_agent["read_only"] and worker["read_only"]
    assert local_agent["cap_drop"] == worker["cap_drop"] == ["ALL"]
    assert "agent-state:/var/lib/tangying-agent" in local_agent["volumes"]
    assert "agent-state:/var/lib/tangying-agent" in worker["volumes"]


def test_image_contains_all_agent_roles_and_preflights_edge():
    dockerfile = (ROOT / "Dockerfile.agent").read_text()
    entrypoint = (ROOT / "scripts/agent-entrypoint.sh").read_text()
    ignore = (ROOT / ".dockerignore").read_text()
    for command in ("./cmd/fleet-control-plane", "./cmd/local-agent", "./cmd/edge-worker"):
        assert command in dockerfile
    assert "ARG TARGETARCH" in dockerfile
    assert "!scripts/agent-entrypoint.sh" in ignore
    assert "**/*.env" in ignore and "**/certs/" in ignore
    for role in ("server)", "edge)", "worker)"):
        assert role in entrypoint
    assert entrypoint.count("--check-config") == 2


def test_cloud_server_key_is_group_readable_without_mounting_ca_key():
    certs = (ROOT / "scripts/fleet-certs.sh").read_text()
    up = (ROOT / "scripts/fleet-up.sh").read_text()
    assert 'chmod 640 "$CERT_DIR/fleet-server.key"' in certs
    assert 'chmod 600 "$CERT_DIR"/*.key' in certs
    assert "set_cert_group" in up
    assert "export FLEET_CERT_GID" in up
