"""Export closed Gazebo evidence without console sessions or dotenv files.

Example: python scripts/export_gazebo_field_evidence.py --source /private/evidence \
    --extra /private/alignment001 /private/alignment002 --output /public/new-export

This is a literal known-credential audit, not a general secret detector. It reads
console-session tokens and simple credential assignments in excluded dotenv
files. Source files must be quiescent; do not export an active SQLite writer.
The original files are never changed. SHA256 provides integrity, not authenticity.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

BLOCK = 1024 * 1024
ARCHIVE = "evidence.tar.gz"

VERIFY_SCRIPT = '''#!/usr/bin/env python3
"""Verify this evidence export without extracting or trusting archive paths.

Usage: python verify.py [export-directory]
An independently retained manifest digest is needed to detect replacement of
both archive and manifest. These hashes do not authenticate the experiment.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile


def digest(stream):
    result = hashlib.sha256()
    size = 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(block)
        size += len(block)
    return result.hexdigest(), size


def check(condition, message):
    if not condition:
        raise ValueError(message)


def index(rows):
    result = {}
    for row in rows:
        name = row["path"]
        path = PurePosixPath(name)
        check(isinstance(name, str) and name and not path.is_absolute()
              and ".." not in path.parts and str(path) == name,
              "invalid member identity")
        check(name not in result, "duplicate member identity")
        check(type(row["size"]) is int and row["size"] >= 0
              and isinstance(row["sha256"], str) and len(row["sha256"]) == 64,
              "invalid member fingerprint")
        result[name] = row
    return result


def verify(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    check(manifest["schemaVersion"] == "gazebo.field.evidence.v1", "unsupported manifest")
    check(manifest["archive"]["path"] == "evidence.tar.gz", "unexpected archive identity")
    check(set(manifest["metadata"]) == {"redactions.json", "verify.py"}, "metadata set mismatch")
    for name, expected in manifest["metadata"].items():
        check(not (directory / name).is_symlink(), "metadata symlink forbidden")
        with (directory / name).open("rb") as stream:
            actual = digest(stream)
        check(actual == (expected["sha256"], expected["size"]), "metadata fingerprint mismatch")
    archive = directory / "evidence.tar.gz"
    check(not archive.is_symlink(), "archive symlink forbidden")
    with archive.open("rb") as stream:
        actual = digest(stream)
    check(actual == (manifest["archive"]["sha256"], manifest["archive"]["size"]),
          "archive fingerprint mismatch")
    expected = index(manifest["publicMembers"])
    originals = index(manifest["sourceFiles"])
    included = {name: row for name, row in originals.items() if row["disposition"] == "included"}
    excluded = {name for name, row in originals.items() if row["disposition"] == "excluded"}
    check(len(included) + len(excluded) == len(originals), "invalid disposition")
    check(set(expected) == set(included), "public inventory omits an original file")
    for name, row in expected.items():
        check((row["sha256"], row["size"]) == (included[name]["sha256"], included[name]["size"]),
              "public content differs from original")
    redactions = json.loads((directory / "redactions.json").read_text())
    redacted_names = [row["path"] for row in redactions["files"]]
    check(len(set(redacted_names)) == len(redacted_names)
          and set(redacted_names) == excluded, "redaction inventory mismatch")
    seen = set()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            check(member.isfile() and not member.issym() and not member.islnk(),
                  "non-regular archive member")
            check(member.name in expected and member.name not in seen, "unexpected or duplicate member")
            row = expected[member.name]
            check(member.size == row["size"], "member size mismatch")
            with bundle.extractfile(member) as stream:
                actual = digest(stream)
            check(actual == (row["sha256"], row["size"]), "member fingerprint mismatch")
            seen.add(member.name)
    check(seen == set(expected), "archive is missing members")
    return len(seen)


if __name__ == "__main__":
    try:
        count = verify(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError):
        print("Evidence verification FAILED; archive or manifest is incomplete or changed.", file=sys.stderr)
        raise SystemExit(1)
    print(f"Verified {count} archive members, original fingerprints, and redaction inventory.")
'''


class ExportRefused(ValueError):
    """Messages must never include input paths, file contents, or credentials."""


def exclusion(name):
    if name == "console-session":
        return "console_session_credential"
    if name == ".env" or name.endswith(".env") or name.startswith(".env.") or ".env." in name:
        return "dotenv_session_or_configuration"
    return ""


def signature(path):
    info = path.lstat()
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def inventory(root, prefix):
    rows = []
    if root.is_symlink() or not root.is_dir():
        raise ExportRefused("Source roots must be real directories, never symlinks.")
    def unreadable(_error):
        raise ExportRefused("Source directory cannot be completely enumerated.")

    for current, directories, files in os.walk(root, followlinks=False, onerror=unreadable):
        for name in sorted(directories + files):
            path = Path(current) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ExportRefused("Source contains a symlink or special file; export refused.")
            if stat.S_ISREG(info.st_mode):
                rows.append({"file": path, "path": prefix + "/" + path.relative_to(root).as_posix(),
                             "signature": signature(path), "reason": exclusion(name)})
    return sorted(rows, key=lambda row: row["path"])


def open_original(row):
    if signature(row["file"]) != row["signature"]:
        raise ExportRefused("Source changed during export; stop writers and retry.")
    descriptor = os.open(row["file"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    stream = os.fdopen(descriptor, "rb")
    info = os.fstat(stream.fileno())
    if (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != row["signature"]:
        stream.close()
        raise ExportRefused("Source identity changed during export.")
    return stream


def known_credentials(rows):
    credentials = set()
    for row in rows:
        if not row["reason"]:
            continue
        with open_original(row) as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ExportRefused("Excluded credential file exceeds audit budget.")
        if row["reason"] == "console_session_credential":
            value = data.strip()
            if value:
                credentials.add(value)
        else:
            for line in data.splitlines():
                match = re.match(rb"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
                if match and re.search(rb"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|SESSION)", match[1].upper()):
                    value = match[2]
                    # Parse quotes/comments without shell execution or variable
                    # expansion. Keep the raw form as well for literal logs.
                    if value:
                        credentials.add(value)
                        try:
                            parts = shlex.split(value.decode("utf-8"), comments=True)
                        except (ValueError, UnicodeError):
                            raise ExportRefused("Cannot safely audit a dotenv credential assignment.") from None
                        if len(parts) == 1 and parts[0]:
                            credentials.add(parts[0].encode())
    return tuple(credentials)


def audit(data, credentials):
    if any(value in data for value in credentials):
        raise ExportRefused("Known credential found in prospective public output; export refused.")


class AuditedReader:
    def __init__(self, stream, credentials):
        self.stream, self.credentials = stream, credentials
        self.hash, self.size, self.tail = hashlib.sha256(), 0, b""
        self.overlap = max((len(value) - 1 for value in credentials), default=0)

    def read(self, size=-1):
        data = self.stream.read(size)
        audit(self.tail + data, self.credentials)
        if self.overlap:
            self.tail = (self.tail + data)[-self.overlap:]
        self.hash.update(data)
        self.size += len(data)
        return data

    def fingerprint(self):
        return {"sha256": self.hash.hexdigest(), "size": self.size}


def fingerprint(path):
    with path.open("rb") as stream:
        reader = AuditedReader(stream, ())
        while reader.read(BLOCK):
            pass
    return reader.fingerprint()


def write_json(path, data, credentials):
    encoded = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    audit(encoded, credentials)
    path.write_bytes(encoded)


def export_evidence(source, extras, output):
    source, extras, output = Path(source), [Path(path) for path in extras], Path(output)
    roots = [(source, "evidence")] + [(path, "extras/" + path.name) for path in extras]
    if len({prefix for _, prefix in roots}) != len(roots):
        raise ExportRefused("Extra directories must have distinct names.")
    resolved = [root.resolve() for root, _ in roots]
    destination = output.absolute()
    if destination.exists() or destination.is_symlink():
        raise ExportRefused("Output must be a new directory; existing output is never overwritten.")
    if not destination.parent.is_dir():
        raise ExportRefused("Output parent must already exist.")
    if any(destination.resolve().is_relative_to(root) for root in resolved):
        raise ExportRefused("Output must be outside every source tree.")
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for index, a in enumerate(resolved) for b in resolved[index + 1:]):
        raise ExportRefused("Source roots must not overlap.")
    rows = [row for root, prefix in roots for row in inventory(root, prefix)]
    credentials = known_credentials(rows)
    for row in rows:
        audit(row["path"].encode(), credentials)
        with open_original(row) as stream:
            reader = AuditedReader(stream, () if row["reason"] else credentials)
            while reader.read(BLOCK):
                pass
        row.update(reader.fingerprint())
    originals = [{key: row[key] for key in ("path", "sha256", "size")} |
                 {"disposition": "excluded" if row["reason"] else "included"} for row in rows]
    public = [{key: row[key] for key in ("path", "sha256", "size")} for row in rows if not row["reason"]]
    redactions = {"schemaVersion": "gazebo.field.redactions.v1", "mode": "whole_file_exclusion",
        "files": [{"path": row["path"], "reason": row["reason"]} for row in rows if row["reason"]],
        "audit": {"knownCredentialCount": len(credentials), "policy": "literal_bytes_and_member_names",
                  "sources": ["console-session", "simple dotenv credential assignments"],
                  "generalSecretDetection": False, "sourceFilesModified": False}}
    # Staging is private and cleaned on every failure. No partial public output.
    with tempfile.TemporaryDirectory(prefix=".gazebo-evidence-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "export"
        staging.mkdir(mode=0o700)
        with ((staging / ARCHIVE).open("wb") as raw,
              gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
              tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as bundle):
            for row in rows:
                if row["reason"]:
                    continue
                info = tarfile.TarInfo(row["path"])
                info.size, info.mode, info.mtime = row["size"], 0o644, 0
                with open_original(row) as stream:
                    reader = AuditedReader(stream, credentials)
                    bundle.addfile(info, reader)
                    if reader.fingerprint() != {key: row[key] for key in ("sha256", "size")}:
                        raise ExportRefused("Source content changed during archive creation.")
        audit(VERIFY_SCRIPT.encode(), credentials)
        (staging / "verify.py").write_text(VERIFY_SCRIPT)
        write_json(staging / "redactions.json", redactions, credentials)
        manifest = {"schemaVersion": "gazebo.field.evidence.v1",
            "archive": {"path": ARCHIVE, **fingerprint(staging / ARCHIVE)},
            "sourceFiles": originals, "publicMembers": public,
            "metadata": {name: fingerprint(staging / name) for name in ("redactions.json", "verify.py")},
            "integrityBoundary": "Retain an independent manifest digest; hashes are not authentication."}
        write_json(staging / "manifest.json", manifest, credentials)
        final_rows = [row for root, prefix in roots for row in inventory(root, prefix)]
        if [(row["path"], row["signature"]) for row in rows] != [(row["path"], row["signature"]) for row in final_rows]:
            raise ExportRefused("Source inventory changed during export; stop writers and retry.")
        # Verify through the exact standalone code shipped to readers.
        verified = subprocess.run([sys.executable, str(staging / "verify.py")],
                                  check=False, capture_output=True)
        if verified.returncode:
            raise ExportRefused("Standalone verification failed before publication.")
        # mkdir excludes accidental overwrite if another exporter won the race.
        destination.mkdir(mode=0o700)
        try:
            for path in staging.iterdir():
                os.replace(path, destination / path.name)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return {"publicMemberCount": len(public), "excludedFileCount": len(redactions["files"]),
            "archiveSha256": manifest["archive"]["sha256"],
            "manifestSha256": fingerprint(destination / "manifest.json")["sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--extra", type=Path, nargs="+", action="extend", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = export_evidence(args.source, args.extra, args.output)
    except ExportRefused as error:
        parser.exit(1, "Export refused: " + str(error) + "\n")
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError):
        # Input filenames and underlying parser errors may themselves contain a
        # credential, so neither a traceback nor the original exception escapes.
        parser.exit(1, "Export refused: unreadable, invalid, or changing evidence.\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
