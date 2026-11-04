#!/usr/bin/env python3
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
