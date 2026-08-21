from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from tangying_robocasa.model_identity import model_content_hash


def mjcf_with_mesh(mesh_path: Path) -> ET.Element:
    root = ET.Element("mujoco", {"model": "fixture"})
    asset = ET.SubElement(root, "asset")
    ET.SubElement(asset, "mesh", {"name": "fixture-mesh", "file": str(mesh_path)})
    return root


def test_model_hash_is_path_independent_and_asset_sensitive(tmp_path: Path) -> None:
    left = tmp_path / "left" / "mesh.stl"
    right = tmp_path / "right" / "mesh.stl"
    left.parent.mkdir()
    right.parent.mkdir()
    left.write_bytes(b"same-mesh")
    right.write_bytes(b"same-mesh")

    assert model_content_hash(mjcf_with_mesh(left)) == model_content_hash(
        mjcf_with_mesh(right)
    )

    right.write_bytes(b"changed-mesh")

    assert model_content_hash(mjcf_with_mesh(left)) != model_content_hash(
        mjcf_with_mesh(right)
    )
