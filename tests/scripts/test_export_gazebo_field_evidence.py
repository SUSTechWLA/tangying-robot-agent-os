"""Public archive checks with real files, SQLite, gzip, and standalone verifier."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts.export_gazebo_field_evidence import BLOCK, ExportRefused, export_evidence

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/export_gazebo_field_evidence.py"


def test_complete_archive_preserves_failed_logs_sqlite_sensors_and_extra_evidence(tmp_path):
    source, extra, output = tmp_path / "private", tmp_path / "alignment003", tmp_path / "public"
    (source / "campaign005/ground-a").mkdir(parents=True)
    extra.mkdir()
    (source / "campaign005/ground-a/console-session").write_text("test-session-73c6d8a2c5d4e091\n")
    (source / ".env.local").write_text("API_KEY=test-env-2e98620b290efc51\n")
    (source / "failed-controller.jsonl").write_text('{"type":"FAILED","reason":"timeout"}\n')
    (source / "sensor.json").write_text('{"ranges":[1.2,3.4],"真实测量":true}\n')
    (extra / "metrics.json").write_text('{"accepted":true}\n')
    database = source / "campaign005/ground-a/runtime.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE commands (id TEXT, state TEXT)")
        connection.execute("INSERT INTO commands VALUES ('cmd-1', 'FAILED')")
    before = {path: path.read_bytes() for root in (source, extra) for path in root.rglob("*") if path.is_file()}
    summary = export_evidence(source, [extra], output)
    assert summary["publicMemberCount"] == 4 and summary["excludedFileCount"] == 2
    manifest = json.loads((output / "manifest.json").read_text())
    with tarfile.open(output / "evidence.tar.gz") as archive:
        assert len(archive.getmembers()) == 4
        assert archive.extractfile("evidence/campaign005/ground-a/runtime.sqlite").read() == before[database]
        assert archive.extractfile("evidence/failed-controller.jsonl").read() == before[source / "failed-controller.jsonl"]
    assert all(path.read_bytes() == original for path, original in before.items())
    assert len(manifest["sourceFiles"]) == 6
    assert all(len(row["sha256"]) == 64 for row in manifest["sourceFiles"])
    verified = subprocess.run([sys.executable, str(output / "verify.py")], cwd=tmp_path,
                              check=False, capture_output=True, text=True)
    assert verified.returncode == 0 and "Verified 4 archive members" in verified.stdout
    with pytest.raises(ExportRefused, match="new directory"):
        export_evidence(source, [extra], output)
    assert (output / "evidence.tar.gz").exists()


@pytest.mark.parametrize("unsafe", ["chunk_boundary", "filename", "dotenv_leak", "symlink"])
def test_export_refuses_credentials_or_symlinks_without_output_or_secret_diagnostics(tmp_path, unsafe):
    source, output = tmp_path / "private", tmp_path / "public"
    source.mkdir()
    token = "secret-session-367ae041cd9d8b2f"
    (source / "console-session").write_text(token + "\n")
    if unsafe == "chunk_boundary":
        # Split the secret across scanner blocks; checking chunks independently
        # would incorrectly publish this binary/log content.
        (source / "runtime.sqlite").write_bytes(b"x" * (BLOCK - 10) + token.encode() + b"tail")
    elif unsafe == "filename":
        (source / token).write_text("ordinary content")
    elif unsafe == "dotenv_leak":
        env_secret = "env-private-ff856aa167c25bce"
        (source / "session.env").write_text("export ROBOT_SESSION_TOKEN='" + env_secret + "' # private\n")
        (source / "runtime.log").write_text(env_secret)
    else:
        (source / "link").symlink_to(source / "console-session")
    result = subprocess.run([sys.executable, str(SCRIPT), "--source", str(source), "--output", str(output)],
                            check=False, capture_output=True, text=True)
    assert result.returncode != 0 and "Export refused" in result.stderr
    assert token not in result.stdout + result.stderr and "Traceback" not in result.stderr
    assert "env-private-ff856aa167c25bce" not in result.stdout + result.stderr
    assert not output.exists() and not list(tmp_path.glob(".gazebo-evidence-*"))
    assert (source / "console-session").read_text() == token + "\n"


@pytest.mark.parametrize("tamper", ["missing", "content", "unexpected"])
def test_standalone_verifier_checks_every_member_even_if_archive_digest_is_updated(tmp_path, tamper):
    source, output = tmp_path / "private", tmp_path / "public"
    source.mkdir()
    (source / "one.log").write_bytes(b"first log\n")
    (source / "two.log").write_bytes(b"second log\n")
    export_evidence(source, [], output)
    archive = output / "evidence.tar.gz"
    contents = {"evidence/one.log": b"first log\n", "evidence/two.log": b"second log\n"}
    if tamper == "missing":
        del contents["evidence/two.log"]
    elif tamper == "content":
        contents["evidence/two.log"] = b"tamper log\n"  # Same byte length.
    else:
        contents["evidence/extra.log"] = b"unrecorded\n"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, data in contents.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["archive"].update(sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), size=archive.stat().st_size)
    manifest_path.write_text(json.dumps(manifest))
    verified = subprocess.run([sys.executable, str(output / "verify.py")], check=False, capture_output=True, text=True)
    assert verified.returncode != 0 and "verification FAILED" in verified.stderr
