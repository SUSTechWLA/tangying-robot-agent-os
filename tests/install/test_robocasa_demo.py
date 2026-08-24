from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/robocasa-demo.sh"


def _parsed_demo(*arguments: str) -> str:
    command = r'''
source "$1"
shift
parse_args "$@"
printf '%s|%s|%s\n' "$DEMO_MODE" "$HUMAN_SPEED" "$OPEN_BROWSER"
'''
    result = subprocess.run(
        ["bash", "-c", command, "robocasa-demo-test", str(SCRIPT), *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_default_demo_leaves_the_fresh_handoff_for_the_users_create_click() -> None:
    assert _parsed_demo() == "interactive|0.04|1"


def test_automatic_handoff_is_an_explicit_demo_mode() -> None:
    assert _parsed_demo("--auto-run", "--no-open", "--human-speed", "0.01") == (
        "auto|0.01|0"
    )


def test_demo_keeps_startup_errors_visible_and_never_follows_logs_forever() -> None:
    script = SCRIPT.read_text()

    assert 'robocasa-fleet.sh" start >/dev/null' not in script
    assert 'tail -n 40 "$ROOT_DIR/logs"/robocasa-*.log' in script


def test_default_demo_removes_stale_local_cloud_volumes_for_a_truly_fresh_run() -> None:
    script = SCRIPT.read_text()

    assert "docker compose down -v --remove-orphans" in script
    assert "stop_cross_worktree_profile" in script


def test_demo_waits_for_the_first_mujoco_world_before_inviting_a_task() -> None:
    script = SCRIPT.read_text()

    assert 'world = call("/v1/world", token=token)' in script
    assert '"red-block" in entities' in script
    assert '{"robot-1", "robot-2"} <= set(robots)' in script
