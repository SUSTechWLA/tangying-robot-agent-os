"""Freeze an allowlisted local simulation evidence set; refuse known credential bytes."""
import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, action="append", default=[])
    args = parser.parse_args()
    forbidden = []
    for path in args.credential_file:
        for line in path.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                if not any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                    continue
                value = value.strip().strip("\"'")
            else:
                value = line.strip()
            if len(value) >= 12:
                forbidden.append(value.encode())
    allowed_dirs = {"initial", "environment", "final"}
    allowed_suffixes = {".json", ".jsonl", ".log", ".txt", ".md", ".py", ".jpg", ".png", ".npz"}
    selected, omitted = [], []
    for path in sorted(args.source.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(args.source)
        top = relative.parts[0]
        scoped = len(relative.parts) == 1 or top in allowed_dirs or top.startswith(("task-", "mapping-"))
        if not scoped or path.suffix not in allowed_suffixes or path.stat().st_size > 5_000_000:
            omitted.append(str(relative))
            continue
        payload = path.read_bytes()
        if any(secret in payload for secret in forbidden):
            raise SystemExit(f"Known credential bytes found; refusing archive member: {relative}")
        selected.append((relative.as_posix(), payload))
    args.output.mkdir(parents=True, exist_ok=False)
    archive = args.output / "evidence.tar.gz"
    manifest = []
    with tarfile.open(archive, "w:gz") as tar:
        for name, payload in selected:
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mode = 0o644
            tar.addfile(member, io.BytesIO(payload))
            manifest.append({"path": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    report = {
        "scope": "Synthetic Gazebo household data and local test records; contains prompts and runtime metadata, not anonymized research data.",
        "source": str(args.source.resolve()),
        "credentialCheck": "Known configured credential bytes checked in each uncompressed selected file; no universal secret-detection claim.",
        "archiveSHA256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "files": manifest, "omitted": omitted,
    }
    (args.output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    verify(args.output)
    print(json.dumps({"members": len(manifest), "archiveBytes": archive.stat().st_size, "verified": True}))


def verify(folder):
    report = json.loads((folder / "manifest.json").read_text())
    archive = folder / "evidence.tar.gz"
    if hashlib.sha256(archive.read_bytes()).hexdigest() != report["archiveSHA256"]:
        raise ValueError("archive digest mismatch")
    expected = {row["path"]: row for row in report["files"]}
    with tarfile.open(archive, "r:gz") as tar:
        if len(tar.getmembers()) != len(expected):
            raise ValueError("archive member count mismatch")
        for member in tar.getmembers():
            row = expected.pop(member.name)
            if not member.isfile() or member.size != row["bytes"]:
                raise ValueError(f"invalid member type/size: {member.name}")
            payload = tar.extractfile(member).read()
            if hashlib.sha256(payload).hexdigest() != row["sha256"]:
                raise ValueError(f"member digest mismatch: {member.name}")
    if expected:
        raise ValueError("missing archive members")


if __name__ == "__main__":
    main()
