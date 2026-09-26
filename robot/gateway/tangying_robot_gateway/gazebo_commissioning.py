"""Authoritative mount metadata generated with the Gazebo SDF."""
import json
import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def commissioning():
    path = os.environ.get("TANGYING_HOME_COMMISSIONING", "")
    if not path:
        return {}
    document = json.loads(Path(path).read_text())
    if document.get("schemaVersion") != "robot.home.commissioning.v1":
        raise ValueError("HOME_COMMISSIONING_INVALID")
    world = Path(os.environ.get("TANGYING_GAZEBO_WORLD", Path(path).parent / "home.sdf"))
    import hashlib
    if hashlib.sha256(world.read_bytes()).hexdigest() != document["worldRevision"]:
        raise ValueError("HOME_WORLD_REVISION_MISMATCH")
    for name, expected in document.get("files", {}).items():
        parent = Path(path).resolve().parent
        file = (parent / name).resolve()
        if not file.is_relative_to(parent) or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise ValueError("HOME_ASSET_REVISION_MISMATCH: "+name)
    return document
