"""Verify public bytes and recompute the compact, source-bound task counts."""
from pathlib import Path
import hashlib
import json
import tarfile

ROOT = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verify_members(archive, listing):
    members = archive.getmembers()
    if [m.name for m in members] != [r["path"] for r in listing]:
        raise ValueError("archive member set changed")
    for member, expected in zip(members, listing, strict=True):
        if (not member.isfile() or member.name.startswith("/")
                or ".." in Path(member.name).parts or member.size != expected["bytes"]
                or sha(archive.extractfile(member).read()) != expected["sha256"]):
            raise ValueError("archive member mismatch: " + member.name)


def main():
    manifest = json.loads((ROOT / "master-manifest.json").read_text())
    files = manifest["files"]
    actual = {str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
              if p.is_file() and p.name != "master-manifest.json" and "__pycache__" not in p.parts}
    if actual != set(files):
        raise ValueError("public file set changed")
    for name, expected in files.items():
        path = (ROOT / name).resolve()
        if ROOT not in path.parents or sha(path.read_bytes()) != expected:
            raise ValueError("public file mismatch: " + name)
    listing = json.loads((ROOT / "archive-members.json").read_text())["files"]
    with tarfile.open(ROOT / "raw-evidence.tar.gz", "r:gz") as archive:
        verify_members(archive, listing)
        ros = json.load(archive.extractfile("ros-final/summary.json"))
        routes = [r for r in ros["trials"] if r["type"] == "natural_language_route"]
        if len(routes) != 9 or not all(r["passed"] for r in routes):
            raise ValueError("recorded DDS routes disagree")
        trio = json.load(archive.extractfile("trio-002/summary.json"))
        if len(trio["episodes"]) != 6 or trio["partial"] != 6 or trio["softwareErrors"] != 0:
            raise ValueError("recorded exploration outcomes disagree")
    supplement = json.loads((ROOT / "post-review-members.json").read_text())["files"]
    with tarfile.open(ROOT / "post-review.tar.gz", "r:gz") as archive:
        verify_members(archive, supplement)
        final = json.load(archive.extractfile("validation/final-validation.json"))
        for name, expected in final["sourceHashes"].items():
            if sha(archive.extractfile("sources/" + name).read()) != expected:
                raise ValueError("final source mismatch: " + name)
        for check in final["checks"]:
            if sha(archive.extractfile("validation/" + check["log"]).read()) != check["logSha256"]:
                raise ValueError("final validation log mismatch")
    print(json.dumps({"verifiedPublicFiles": len(files), "verifiedArchiveMembers": len(listing),
                      "verifiedSupplementMembers": len(supplement),
                      "ddsRecordedTasks": len(routes), "trioPartialTasks": 6,
                      "physicalValidation": False, "physicalRerun": False}))


if __name__ == "__main__":
    main()
