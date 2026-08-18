from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_documents_every_supported_install_role():
    readme = (ROOT / "README.md").read_text()
    for command in (
        "./install.sh sim",
        "./install.sh local",
        "./install.sh robot-pi",
    ):
        assert command in readme


def test_readme_robot_agent_commands_exist_in_cli_help():
    readme = (ROOT / "README.md").read_text()
    completed = subprocess.run(
        ["go", "run", "./cmd/robot-agent", "help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for command in ("doctor", "configure", "pair", "start", "status", "logs", "demo"):
        assert f"robot-agent {command}" in readme
        assert command in completed.stdout


def test_readme_links_durable_design_assets_and_rejects_cloud_runtime():
    readme = (ROOT / "README.md").read_text()
    assert "docs/superpowers/specs/2026-08-18-local-first-runtime-design.md" in readme
    assert "docs/superpowers/plans/2026-08-18-local-first-runtime.md" in readme
    assert "旧 `./install.sh cloud`" in readme
    assert "CLOUD_URL=" not in readme


def test_readme_local_markdown_links_resolve():
    readme = (ROOT / "README.md").read_text()
    for target in _local_markdown_targets(readme):
        assert (ROOT / target).exists(), f"README link does not exist: {target}"


def _local_markdown_targets(markdown: str) -> set[str]:
    targets = set()
    for fragment in markdown.split("](")[1:]:
        target = fragment.split(")", 1)[0].split("#", 1)[0]
        if target and "://" not in target and not target.startswith("#"):
            targets.add(target)
    return targets
