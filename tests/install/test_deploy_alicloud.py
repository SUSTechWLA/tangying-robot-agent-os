"""Deployment packaging must not read outside a reviewed committed source tree."""
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_deployment_archives_committed_source_and_preserves_remote_network_policy(tmp_path):
    repo = tmp_path / "project"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/deploy-alicloud.sh", repo / "scripts/deploy-alicloud.sh")
    (repo / "go.mod").write_text("module fixture\ngo 1.26\n")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / ".env").write_text("SECRET=must-not-upload")
    (repo / "untracked.txt").write_text("do not include")
    (tmp_path / "sibling-secret.txt").write_text("not our project")
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    programs = {
        "go": '#!/bin/sh\nmkdir vendor\nprintf "fixture" > vendor/fixture\n',
        "ssh": '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DEPLOY_TEST_LOG"\n',
        "scp": '#!/bin/sh\nfor arg in "$@"; do\n if [ -f "$arg" ]; then cp "$arg" "$DEPLOY_TEST_ARCHIVE"; fi\ndone\n',
    }
    for name, content in programs.items():
        (fakebin / name).write_text(content)
        (fakebin / name).chmod(0o755)
    log = tmp_path / "ssh.log"
    archive = tmp_path / "uploaded.tar.gz"
    env = dict(os.environ, PATH=str(fakebin) + os.pathsep + os.environ["PATH"],
               ALICLOUD_SSH_HOST="robot-cloud.test", DEPLOY_TEST_LOG=str(log), DEPLOY_TEST_ARCHIVE=str(archive))
    result = subprocess.run(["bash", "scripts/deploy-alicloud.sh"], cwd=repo, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "./go.mod" in names and "./vendor/fixture" in names
    assert not any(name.endswith((".env", "untracked.txt", "sibling-secret.txt")) for name in names)
    commands = log.read_text()
    assert "StrictHostKeyChecking=yes" in commands
    assert "bash scripts/fleet-up.sh up --build" in commands
    assert "allow all" not in commands
    (repo / "go.mod").write_text("changed")
    result = subprocess.run(["bash", "scripts/deploy-alicloud.sh"], cwd=repo, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "commit reviewed" in result.stderr
