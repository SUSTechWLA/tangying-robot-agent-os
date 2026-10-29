"""Check the bounded archive without extracting or executing its contents."""
from pathlib import Path
import hashlib
import json
import tarfile

base = Path(__file__).resolve().parent
manifest = json.loads((base / "evidence-manifest.json").read_text())
archive = base / manifest["archive"]["path"]
assert archive.stat().st_size == manifest["archive"]["sizeBytes"]
assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest["archive"]["sha256"]
expected = {r["path"]: r for r in manifest["members"]}
seen = set()
with tarfile.open(archive, "r:gz") as tar:
    for member in tar:
        assert member.isfile() and member.name in expected and member.name not in seen
        assert not Path(member.name).is_absolute() and ".." not in Path(member.name).parts
        data = tar.extractfile(member).read()
        record = expected[member.name]
        assert len(data) == member.size == record["sizeBytes"]
        assert hashlib.sha256(data).hexdigest() == record["sha256"]
        seen.add(member.name)
assert seen == set(expected)
print(json.dumps({"status": "PASS", "members": len(seen), "archiveSHA256": manifest["archive"]["sha256"]}))
