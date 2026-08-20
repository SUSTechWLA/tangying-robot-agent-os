from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_robocasa_fleet_launches_one_sim_and_two_edges():
    script = (ROOT / "scripts/robocasa-fleet.sh").read_text()

    assert "tangying_robocasa.fleet_server" in script
    assert '"$EDGE_WORKER"' in script
    assert "EDGE_ADAPTER=robocasa" in script
    assert "EDGE_WORLD_ID=robocasa-handoff-v1" in script
    assert "EDGE_TRANSFORM_REVISION=robocasa-world-v1" in script
    assert 'start_edge robot-1 "$SIM_PORT_1"' in script
    assert 'start_edge robot-2 "$SIM_PORT_2"' in script


def test_robocasa_fleet_has_lifecycle_and_cloud_bootstrap_contract():
    script = (ROOT / "scripts/robocasa-fleet.sh").read_text()

    assert '"$SCRIPT_DIR/fleet-up.sh" up --build' in script
    assert "start)" in script
    assert "stop)" in script
    assert "status)" in script
    assert "handoff|demo)" in script
    assert "run) run_foreground" in script
    assert "started-cloud" in script
    assert "robocasa" in script
    assert 'wait_for_port "$SIM_PORT_1"' in script
    assert 'wait_for_port "$SIM_PORT_2"' in script
    assert 'go build -o "$EDGE_WORKER" ./cmd/edge-worker' in script
    assert "FLEET_WORLD_ID=robocasa-handoff-v1" in script
    assert "trap stop EXIT INT TERM" in script


def test_makefile_exposes_robocasa_fleet_targets():
    makefile = (ROOT / "Makefile").read_text()

    assert "robocasa-fleet:" in makefile
    assert "robocasa-handoff:" in makefile
    assert "scripts/robocasa-fleet.sh" in makefile


def test_fleet_up_migrates_legacy_shared_device_token():
    script = (ROOT / "scripts/fleet-up.sh").read_text()

    assert "migrate_legacy_env" in script
    assert "FLEET_DEVICE_TOKEN" in script
    assert "migrated legacy shared device token" in script
    assert "persist_requested_world_id" in script
    assert "FLEET_WORLD_ID=${FLEET_WORLD_ID:-fleet-default}" in script


def test_fleet_up_does_not_print_generated_secrets_during_startup():
    script = (ROOT / "scripts/fleet-up.sh").read_text()

    assert 'operator password: $operator_pass' not in script
    assert 'device credentials: $device_credentials' not in script
    assert 'chmod 600 "$ENV_FILE"' in script


def test_alicloud_deploy_keeps_generated_credentials_out_of_logs():
    script = (ROOT / "scripts/deploy-alicloud.sh").read_text()

    assert "operator password:" not in script
    assert "robot-1 token:" not in script
    assert "robot-2 token:" not in script
    assert "chmod 600 .env" in script


def test_cloud_build_excludes_simulator_datasets_and_local_caches():
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()

    for path in ("datasets/", "XLeRobot/", ".venv/", ".gocache/", ".gomodcache/"):
        assert path in dockerignore


def test_cloud_exposes_browser_safe_http_only_on_loopback():
    compose = (ROOT / "deploy/cloud/docker-compose.yml").read_text()
    nginx = (ROOT / "deploy/cloud/nginx.conf").read_text()

    assert '127.0.0.1:${FLEET_LOOPBACK_HTTP_PORT:-18080}:80' in compose
    assert "listen 80;" in nginx
    assert "proxy_pass http://fleet_control_plane;" in nginx
    assert "proxy_set_header Host $http_host;" in nginx
    assert "proxy_set_header Host $host;" not in nginx
    assert "REDIS_ADDR: redis:6379" in compose
