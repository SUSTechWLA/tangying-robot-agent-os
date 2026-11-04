"""Freeze an allowlisted simulation record, refusing known credentials.

Run only after writers have stopped. This does not anonymize prompts or metadata.
"""

import argparse
import fnmatch
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path

ROOT_PATTERNS = (
    "plan.json", "initial-*.json", "*-before.json", "reuse-build.json",
    "installed-critical-sources.json", "guard-after-survey-001.log",
    "build-*.log", "startup*.log", "ros-observations*.jsonl",
    "ros-observer*.log", "survey-*-console.log", "survey-*-bounded-*.json*",
    "rtabmap-snapshot-after-survey-001.json", "model-usage.jsonl", "task-001-metrics.json",
)
DIRECTORY_PATTERN = re.compile(r"^(?:survey-\d+|cohort-\d+|task-\d+(?:-closure|-patrol-audit)?|verification|tools)$")
TEXT_SUFFIXES = {".json", ".jsonl", ".log", ".txt", ".md", ".py", ".geojson", ".exit"}
MAX_BYTES = 64_000_000
BINARY_PATHS = {"task-001-closure/journal-backup.sqlite"} | {
    f"task-001-patrol-audit/{record}-{kind}.png"
    for record in (
        "4187e613a4211fc1b0d953dafda885d85b68f7800bd29327375de27e97315230",
        "60394799620c098f3311d042bd85cafc102e9933127f9c55e7d9e43c2b7df8de",
        "69c8379946eb54a2916973a9e49b33a3d02d9600f81091437a981399fc95c181",
        "75c51b9276413013a851f7a59da02ad0a345f1105f86f561dd7d1887e4c4bebc",
        "acf87b550b2e4517606518a15210e5403767bbc7f2f22a39d0bd7d4d1e55dcae",
        "af698576ee0b3ab4dac06c57d00aff1ad89ac32e29557f78caee965236338c87",
    )
    for kind in ("rgb", "depth")
}


def known_secrets(paths):
    """Parse only credential-like env keys or bare token files; never return them to logs."""
    secrets = set()
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                if not re.search(r"KEY|TOKEN|SECRET|PASSWORD", key, re.IGNORECASE):
                    continue
                value = value.strip().strip("\"'")
            else:
                value = line
            if len(value) >= 8:
                secrets.add(value.encode())
    if not secrets:
        raise ValueError("no credential values found in the supplied credential files")
    return secrets


def allowed(relative):
    parts = relative.parts
    if relative.as_posix() in BINARY_PATHS:
        return True
    if len(parts) == 1:
        return any(fnmatch.fnmatchcase(parts[0], pattern) for pattern in ROOT_PATTERNS)
    return bool(DIRECTORY_PATTERN.fullmatch(parts[0])) and relative.suffix in TEXT_SUFFIXES


def select(source, secrets):
    selected, omitted = [], []
    for path in sorted(source.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(source)
        if not allowed(rel) or path.stat().st_size > MAX_BYTES:
            omitted.append(rel.as_posix())
            continue
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"source changed while reading: {rel}")
        if rel.as_posix() not in BINARY_PATHS:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"non-UTF-8 data in text-only scope: {rel}") from exc
        if any(secret in data for secret in secrets):
            raise ValueError(f"known credential bytes found; refusing member: {rel}")
        selected.append((rel.as_posix(), data))
    if not selected:
        raise ValueError("no allowed evidence selected")
    return selected, omitted


def verify(folder):
    report = json.loads((folder / "manifest.json").read_text())
    archive = folder / "evidence.tar.gz"
    if hashlib.sha256(archive.read_bytes()).hexdigest() != report["archiveSHA256"]:
        raise ValueError("archive digest mismatch")
    expected = {row["path"]: row for row in report["files"]}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            row = expected.pop(member.name)
            if not member.isfile() or member.size != row["bytes"]:
                raise ValueError(f"invalid archive member: {member.name}")
            if hashlib.sha256(tar.extractfile(member).read()).hexdigest() != row["sha256"]:
                raise ValueError(f"member digest mismatch: {member.name}")
    if expected:
        raise ValueError("missing archive members")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, action="append", required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    if output == source or source in output.parents:
        raise ValueError("output must be outside the source tree")
    secrets = known_secrets(args.credential_file)
    selected, omitted = select(source, secrets)
    missing = BINARY_PATHS - {name for name, _ in selected}
    if missing:
        raise ValueError(f"missing required audit evidence: {sorted(missing)}")
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "evidence.tar.gz"
    rows = []
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in selected:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
            rows.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    report = {
        "scope": "Synthetic Gazebo experiment records; prompts and runtime metadata are not anonymized.",
        "credentialCheck": "Known credentials checked in uncompressed selected files; not general secret detection.",
        "limits": "Text plus one explicitly named SQLite snapshot and twelve explicitly named RGB/depth PNGs; other binaries, databases, configuration, and files over 64 MB omitted. Verify full omitted list.",
        "source": str(source), "archiveSHA256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "files": rows, "omitted": omitted,
    }
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    verify(output)
    print(json.dumps({"members": len(rows), "archiveBytes": archive.stat().st_size, "verified": True}))


if __name__ == "__main__":
    main()
