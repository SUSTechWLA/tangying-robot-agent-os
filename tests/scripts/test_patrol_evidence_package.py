import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docs/experiments/2026-10-30-patrol-evidence/package_evidence.py"
spec = importlib.util.spec_from_file_location("patrol_evidence", SCRIPT)
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def test_selection_excludes_configuration_and_binary(tmp_path):
    (tmp_path / "plan.json").write_text("{}")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/local.env").write_text("KEY=dont-export")
    (tmp_path / "survey-001").mkdir()
    (tmp_path / "survey-001/failure.json").write_text('{"state":"failed"}')
    (tmp_path / "survey-001/map.sqlite").write_bytes(b"test")
    selected, omitted = package.select(tmp_path, {b"secret-password-value"})
    assert {name for name, _ in selected} == {"plan.json", "survey-001/failure.json"}
    assert set(omitted) == {"config/local.env", "survey-001/map.sqlite"}


def test_refuses_known_credential_without_printing_value(tmp_path):
    secret = "secret-password-value"
    (tmp_path / "plan.json").write_text(secret)
    with pytest.raises(ValueError) as exc:
        package.select(tmp_path, {secret.encode()})
    assert secret not in str(exc.value)


def test_reads_only_credential_env_keys(tmp_path):
    path = tmp_path / "local.env"
    path.write_text("MODEL=test-model\nOPENAI_API_KEY=secret-password-value\n")
    assert package.known_secrets([path]) == {b"secret-password-value"}


def test_rejects_binary_disguised_as_text(tmp_path):
    (tmp_path / "plan.json").write_bytes(b"\xff")
    with pytest.raises(ValueError, match="non-UTF-8"):
        package.select(tmp_path, {b"secret-password-value"})


def test_only_exact_audit_binaries_are_allowed_and_scanned(tmp_path):
    for name in package.BINARY_PATHS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89\xffsafe binary")
    extra = tmp_path / "task-001-patrol-audit/unrelated-rgb.png"
    extra.write_bytes(b"other image")
    extra_db = tmp_path / "task-001-closure/other.sqlite"
    extra_db.write_bytes(b"other database")
    (tmp_path / "task-001-metrics.json").write_text("{}")
    selected, omitted = package.select(tmp_path, {b"secret-password-value"})
    assert {name for name, _ in selected} == package.BINARY_PATHS | {"task-001-metrics.json"}
    assert set(omitted) == {"task-001-patrol-audit/unrelated-rgb.png", "task-001-closure/other.sqlite"}
    (tmp_path / next(iter(package.BINARY_PATHS))).write_bytes(b"\x89secret-password-value")
    with pytest.raises(ValueError, match="known credential bytes"):
        package.select(tmp_path, {b"secret-password-value"})


def test_end_to_end_archive_integrity_and_no_overwrite(tmp_path, monkeypatch):
    source = tmp_path / "input"
    source.mkdir()
    (source / "plan.json").write_text('{"expected":"inspection"}')
    for name in package.BINARY_PATHS:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89\xffbinary evidence")
    credential = tmp_path / "token"
    credential.write_text("secret-password-value")
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", [
        "package", "--source", str(source), "--output", str(output),
        "--credential-file", str(credential),
    ])
    package.main()
    package.verify(output)
    with pytest.raises(FileExistsError):
        package.main()
