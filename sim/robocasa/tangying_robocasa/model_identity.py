"""Portable content identities for expanded MJCF models."""

from __future__ import annotations

import copy
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path


def model_content_hash(root: ET.Element) -> str:
    """Hash MJCF content and referenced asset bytes without checkout paths."""

    canonical = copy.deepcopy(root)
    for element in canonical.iter():
        file_name = element.get("file")
        if file_name:
            asset = Path(file_name).resolve()
            if not asset.is_file():
                raise FileNotFoundError(str(asset))
            element.set(
                "file", f"sha256:{hashlib.sha256(asset.read_bytes()).hexdigest()}"
            )
    payload = ET.tostring(canonical, encoding="utf-8", short_empty_elements=True)
    return hashlib.sha256(payload).hexdigest()
