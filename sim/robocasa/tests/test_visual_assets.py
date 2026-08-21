from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from pygltflib import GLTF2
from tangying_robocasa.composer import ComposedScene, SceneConfig, compose_handoff_scene
from tangying_robocasa.gltf_export import CANONICAL_JOINTS
from tangying_robocasa.visual_assets import (
    VisualAssetManifest,
    export_visual_bundle,
    with_hash_query,
)


@pytest.fixture(scope="module")
def composed_scene() -> ComposedScene:
    pytest.importorskip("robocasa")
    return compose_handoff_scene(SceneConfig())


@pytest.fixture(scope="module")
def exported_bundle(
    tmp_path_factory: pytest.TempPathFactory, composed_scene: ComposedScene
) -> tuple[Path, VisualAssetManifest]:
    output_dir = tmp_path_factory.mktemp("visual-assets")
    return output_dir, export_visual_bundle(composed_scene, output_dir)


@pytest.mark.robocasa
def test_bundle_excludes_dynamic_bodies_and_keeps_real_fixtures(
    exported_bundle: tuple[Path, VisualAssetManifest], composed_scene: ComposedScene
) -> None:
    output_dir, manifest = exported_bundle
    scene = GLTF2().load_binary(str(output_dir / "scene.glb"))
    names = {node.name for node in scene.nodes}

    assert manifest.schema_version == "tangying.visual-asset.v1"
    assert manifest.scene_id == "robocasa-handoff-v1"
    assert manifest.model_hash == composed_scene.model_hash
    assert {"robot-1__chassis", "robot-2__chassis", "red-block"}.isdisjoint(names)
    assert {"floor_room_main", "sink_main_group_main", "sink_main_group_g0"} <= names
    assert scene.meshes


@pytest.mark.robocasa
def test_manifest_hashes_assets_and_serializes_the_complete_binding(
    exported_bundle: tuple[Path, VisualAssetManifest], composed_scene: ComposedScene
) -> None:
    output_dir, manifest = exported_bundle
    payload = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    binding = json.loads((output_dir / "xlerobot.binding.json").read_text(encoding="utf-8"))
    expected_files = {"scene.glb", "xlerobot.glb", "xlerobot.binding.json"}

    assert payload == {
        "contentHashes": manifest.content_hashes,
        "licenses": ["LICENSE-XLeRobot", "PROVENANCE-XLeRobot.md", "LICENSE-RoboCasa"],
        "modelHash": composed_scene.model_hash,
        "robotModels": {
            "xlerobot": {
                "asset": f"xlerobot.glb?v={manifest.content_hashes['xlerobot.glb']}",
                "binding": (
                    f"xlerobot.binding.json?v={manifest.content_hashes['xlerobot.binding.json']}"
                ),
            }
        },
        "sceneAsset": f"scene.glb?v={manifest.content_hashes['scene.glb']}",
        "sceneId": "robocasa-handoff-v1",
        "schemaVersion": "tangying.visual-asset.v1",
        "units": "meter",
        "upAxis": "Z",
        "worldFrame": "world",
    }
    assert set(manifest.content_hashes) == expected_files
    for file_name, expected_hash in manifest.content_hashes.items():
        assert re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        assert hashlib.sha256((output_dir / file_name).read_bytes()).hexdigest() == expected_hash

    assert set(binding) == set(CANONICAL_JOINTS)
    assert all(
        set(entry) == {"axis", "direction", "maximum", "minimum", "node", "offset"}
        for entry in binding.values()
    )
    robot = GLTF2().load_binary(str(output_dir / "xlerobot.glb"))
    robot_nodes = {node.name for node in robot.nodes}
    assert {entry["node"] for entry in binding.values()} <= robot_nodes


@pytest.mark.robocasa
def test_bundle_includes_license_and_provenance_files(
    exported_bundle: tuple[Path, VisualAssetManifest],
) -> None:
    output_dir, _manifest = exported_bundle

    assert "Apache License" in (output_dir / "LICENSE-XLeRobot").read_text(encoding="utf-8")
    provenance = (output_dir / "PROVENANCE-XLeRobot.md").read_text(encoding="utf-8")
    assert "3d14695e40c9c68229c0aacffca6053c75cd3eb6" in provenance
    assert "not a calibrated digital twin" in provenance
    assert "MIT License" in (output_dir / "LICENSE-RoboCasa").read_text(encoding="utf-8")


@pytest.mark.robocasa
def test_generated_text_assets_have_no_trailing_whitespace(
    exported_bundle: tuple[Path, VisualAssetManifest],
) -> None:
    output_dir, _manifest = exported_bundle

    for path in output_dir.iterdir():
        if path.suffix in {".glb", ".json"}:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines == [line.rstrip() for line in lines]


@pytest.mark.robocasa
def test_repeated_export_is_byte_identical(
    exported_bundle: tuple[Path, VisualAssetManifest], composed_scene: ComposedScene
) -> None:
    output_dir, manifest = exported_bundle
    before = {path.name: path.read_bytes() for path in output_dir.iterdir() if path.is_file()}

    repeated = export_visual_bundle(composed_scene, output_dir)

    after = {path.name: path.read_bytes() for path in output_dir.iterdir() if path.is_file()}
    assert repeated == manifest
    assert after == before


@pytest.mark.parametrize("digest", ["a" * 63, "A" * 64, "g" * 64])
def test_hash_query_rejects_noncanonical_sha256(digest: str) -> None:
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        with_hash_query("scene.glb", digest)
