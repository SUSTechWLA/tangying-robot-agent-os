"""Build the self-contained browser asset bundle for the RoboCasa handoff scene."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .composer import ComposedScene, SceneConfig
from .gltf_export import build_xlerobot_binding, export_mjcf_visual

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_VERSION = "tangying.visual-asset.v1"
_DYNAMIC_BODY_ROOTS = frozenset({"robot-1__chassis", "robot-2__chassis", "red-block"})
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_LICENSE_FILES = ("LICENSE-XLeRobot", "PROVENANCE-XLeRobot.md", "LICENSE-RoboCasa")


@dataclass(frozen=True, slots=True)
class VisualAssetManifest:
    schema_version: str
    scene_id: str
    model_hash: str
    world_frame: str
    up_axis: str
    units: str
    scene_asset: str
    robot_models: dict[str, dict[str, str]]
    content_hashes: dict[str, str]
    licenses: tuple[str, ...]


def with_hash_query(file_name: str, sha256: str) -> str:
    """Return a stable local asset path with an immutable content-hash query."""

    if _SHA256_PATTERN.fullmatch(sha256) is None:
        raise ValueError("asset digest must be a 64-character lowercase SHA-256")
    return f"{file_name}?v={sha256}"


def _write_json(path: Path, payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _load_xlerobot() -> ET.Element:
    source = Path(SceneConfig().robot_mjcf).resolve()
    root = ET.parse(source).getroot()
    compiler = root.find("compiler")
    mesh_dir = Path(compiler.get("meshdir", ".")) if compiler is not None else Path(".")
    texture_dir = Path(compiler.get("texturedir", ".")) if compiler is not None else Path(".")
    for element in root.iter():
        file_name = element.get("file")
        if not file_name or Path(file_name).is_absolute():
            continue
        directory = (
            mesh_dir
            if element.tag == "mesh"
            else texture_dir
            if element.tag == "texture"
            else Path(".")
        )
        element.set("file", str((source.parent / directory / file_name).resolve()))
    return root


def _robocasa_license() -> Path:
    spec = importlib.util.find_spec("robocasa")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("RoboCasa must be installed to export its browser assets")
    package_dir = Path(next(iter(spec.submodule_search_locations))).resolve()
    license_path = package_dir.parent / "LICENSE"
    if not license_path.is_file():
        raise FileNotFoundError(f"RoboCasa license does not exist: {license_path}")
    return license_path


def _copy_licenses(output_dir: Path) -> None:
    robot_assets = _PROJECT_ROOT / "sim/mujoco/assets/xlerobot"
    sources = {
        "LICENSE-XLeRobot": robot_assets / "LICENSE",
        "PROVENANCE-XLeRobot.md": robot_assets / "PROVENANCE.md",
        "LICENSE-RoboCasa": _robocasa_license(),
    }
    for destination, source in sources.items():
        lines = source.read_text(encoding="utf-8").splitlines()
        normalized = "\n".join(line.rstrip() for line in lines) + "\n"
        (output_dir / destination).write_bytes(normalized.encode("utf-8"))


def export_visual_bundle(scene: ComposedScene, output_dir: Path) -> VisualAssetManifest:
    """Export one deterministic static scene and reusable articulated robot bundle."""

    if scene.scene_id != "robocasa-handoff-v1":
        raise ValueError(f"unsupported visual scene: {scene.scene_id!r}")
    if _SHA256_PATTERN.fullmatch(scene.model_hash) is None:
        raise ValueError("scene model_hash must be a 64-character lowercase SHA-256")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scene_root = ET.fromstring(scene.xml)
    static_export = export_mjcf_visual(
        scene_root,
        output_dir / "scene.glb",
        lambda body_name: body_name not in _DYNAMIC_BODY_ROOTS,
    )

    robot_root = _load_xlerobot()
    robot_export = export_mjcf_visual(
        robot_root,
        output_dir / "xlerobot.glb",
        lambda _body_name: True,
    )
    binding_payload = {
        canonical: asdict(binding)
        for canonical, binding in build_xlerobot_binding(robot_root).items()
    }
    binding_sha = _write_json(output_dir / "xlerobot.binding.json", binding_payload)

    content_hashes = {
        "scene.glb": static_export.sha256,
        "xlerobot.binding.json": binding_sha,
        "xlerobot.glb": robot_export.sha256,
    }
    robot_models = {
        "xlerobot": {
            "asset": with_hash_query("xlerobot.glb", robot_export.sha256),
            "binding": with_hash_query("xlerobot.binding.json", binding_sha),
        }
    }
    manifest = VisualAssetManifest(
        schema_version=_SCHEMA_VERSION,
        scene_id=scene.scene_id,
        model_hash=scene.model_hash,
        world_frame="world",
        up_axis="Z",
        units="meter",
        scene_asset=with_hash_query("scene.glb", static_export.sha256),
        robot_models=robot_models,
        content_hashes=content_hashes,
        licenses=_LICENSE_FILES,
    )
    _copy_licenses(output_dir)
    _write_json(
        output_dir / "manifest.json",
        {
            "schemaVersion": manifest.schema_version,
            "sceneId": manifest.scene_id,
            "modelHash": manifest.model_hash,
            "worldFrame": manifest.world_frame,
            "upAxis": manifest.up_axis,
            "units": manifest.units,
            "sceneAsset": manifest.scene_asset,
            "robotModels": manifest.robot_models,
            "contentHashes": manifest.content_hashes,
            "licenses": list(manifest.licenses),
        },
    )
    return manifest
