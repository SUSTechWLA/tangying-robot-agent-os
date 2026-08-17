from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_demo_is_bounded_to_loopback_and_always_cleans_up():
    script = (ROOT / "scripts/demo.sh").read_text()
    assert "trap cleanup EXIT INT TERM" in script
    assert "127.0.0.1" in script
    assert "mktemp -d" in script
    assert "--dev-insecure" in script
    assert "SUCCEEDED" in script


def test_demo_check_mode_validates_without_starting_stack():
    completed = subprocess.run(
        ["bash", "scripts/demo.sh", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "demo prerequisites: OK" in completed.stdout


def test_compose_binds_services_to_loopback_and_omits_unused_redis():
    compose = (ROOT / "deploy/docker-compose.yml").read_text()
    assert '${CLOUD_BIND:-127.0.0.1}:${CLOUD_PORT:-8080}:8080' in compose
    assert '${POSTGRES_BIND:-127.0.0.1}:${POSTGRES_PORT:-54329}:5432' in compose
    assert "  redis:" not in compose
    assert "redis-server" not in compose

