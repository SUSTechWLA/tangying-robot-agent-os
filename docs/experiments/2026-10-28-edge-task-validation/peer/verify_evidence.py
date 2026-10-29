#!/usr/bin/env python3
"""Verify the archive byte hash and every original evidence member, without extraction."""
from pathlib import Path
import hashlib
import json
import tarfile

base = Path(__file__).resolve().parent
manifest = json.loads((base / "evidence-manifest.json").read_text())
archive = base / manifest["archive"]["path"]
assert archive.stat().st_size == manifest["archive"]["sizeBytes"], "archive size differs"
assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest["archive"]["sha256"], "archive SHA-256 differs"
expected = {manifest["archive"]["rootDirectory"] + "/" + r["path"]: r for r in manifest["members"]}
seen = set()
with tarfile.open(archive, "r:gz") as tar:
    for member in tar:
        assert member.isfile() and member.name in expected and member.name not in seen, "unexpected or non-regular member"
        record = expected[member.name]
        data = tar.extractfile(member).read()
        assert len(data) == member.size == record["sizeBytes"], "member size differs: " + member.name
        assert hashlib.sha256(data).hexdigest() == record["sha256"], "member SHA-256 differs: " + member.name
        seen.add(member.name)
assert seen == set(expected), "missing archive members"
print(json.dumps({"archiveSHA256": manifest["archive"]["sha256"], "verifiedMembers": len(seen), "uncompressedFileBytes": sum(r["sizeBytes"] for r in expected.values()), "status": "PASS"}))
