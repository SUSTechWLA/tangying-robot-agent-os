#!/usr/bin/env python3
"""Prepare pinned AWS Small House meshes for the MuJoCo furnished home."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path, PureWindowsPath

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REVISION = "ff9631ca6d1db9c1ba656498151464b5ab74aafe"
SOURCE_URL = "https://github.com/aws-robotics/aws-robomaker-small-house-world.git"

# These replace only the rendered box on the named body. Collision dimensions and
# body poses remain owned by xlerobot_home.xml.
FURNISHINGS = (
    (
        "living_sofa",
        "aws_robomaker_residential_SofaC_01",
        "aws_SofaC_01_visual.DAE",
        (1.10, 2.20, 0.70),
        None,
    ),
    (
        "living_table",
        "aws_robomaker_residential_CoffeeTable_01",
        "aws_CoffeeTable_01_visual.DAE",
        (1.40, 0.90, 0.70),
        None,
    ),
    # A low cabinet has the counter's proportions and rotates into its long-X footprint.
    (
        "kitchen_counter",
        "aws_robomaker_residential_TVCabinet_01",
        "aws_TVCabinet_01_visual.DAE",
        (0.70, 2.50, 0.90),
        (0.0, 0.0, 90.0),
    ),
    (
        "bedroom_bed",
        "aws_robomaker_residential_Bed_01",
        "aws_Bed_01_visual.DAE",
        (1.50, 2.40, 0.70),
        None,
    ),
)
SURFACES = (
    ("home_floor_material", "aws_robomaker_residential_FloorB_01", "aws_FloorB_01_visual.DAE"),
    ("home_rug_material", "aws_robomaker_residential_Carpet_01", "aws_Carpet_01_visual.DAE"),
)
DECORATIONS = (
    # Kept tight to outer walls, outside commissioned waypoints and task volume.
    (
        "kitchen_refrigerator",
        "aws_robomaker_residential_Refrigerator_01",
        "aws_Refrigerator_01_visual.DAE",
        (0.78, 0.68, 1.82),
        (3.78, 7.72, 0.91),
        (0.39, 0.34, 0.91),
    ),
    (
        "kitchen_cabinet",
        "aws_robomaker_residential_KitchenCabinet_01",
        "aws_KitchenCabinet_01_visual.DAE",
        (1.35, 0.55, 1.75),
        (2.55, 7.90, 0.875),
        (0.675, 0.275, 0.875),
    ),
    (
        "bedroom_wardrobe",
        "aws_robomaker_residential_Wardrobe_01",
        "aws_Wardrobe_01_visual.DAE",
        (0.65, 1.35, 2.05),
        (-4.10, 2.05, 1.025),
        (0.325, 0.675, 1.025),
    ),
    (
        "living_portrait",
        "aws_robomaker_residential_PortraitA_01",
        "aws_PortraitA_01_visual.DAE",
        (1.10, 0.08, 0.72),
        (2.45, -2.40, 1.35),
        None,
    ),
    (
        "kitchen_dining_chair_west",
        "aws_robomaker_residential_ChairA_01",
        "aws_ChairA_01_visual.DAE",
        (0.48, 0.50, 0.90),
        (1.45, 6.85, 0.45),
        (0.24, 0.25, 0.45),
    ),
    (
        "kitchen_dining_chair_east",
        "aws_robomaker_residential_ChairD_01",
        "aws_ChairD_01_visual.DAE",
        (0.52, 0.50, 0.86),
        (3.50, 6.75, 0.43),
        (0.26, 0.25, 0.43),
    ),
    (
        "bedroom_nightstand",
        "aws_robomaker_residential_NightStand_01",
        "aws_NightStand_01_visual.DAE",
        (0.55, 0.45, 0.65),
        (-1.28, 4.72, 0.325),
        (0.275, 0.225, 0.325),
    ),
    (
        "living_wall_light",
        "aws_robomaker_residential_LightC_01",
        "aws_LightC_01_visual.DAE",
        (0.28, 0.30, 0.48),
        (4.34, 0.15, 1.55),
        None,
    ),
    (
        "living_table_vase",
        "aws_robomaker_residential_Vase_01",
        "aws_Vase_01_visual.DAE",
        (0.16, 0.16, 0.34),
        (1.15, -0.80, 0.87),
        (0.08, 0.08, 0.17),
    ),
    (
        "kitchen_tableware",
        "aws_robomaker_residential_Tableware_01",
        "aws_Tableware_01_visual.DAE",
        (0.34, 0.20, 0.20),
        (2.65, 5.95, 1.00),
        (0.17, 0.10, 0.10),
    ),
)

TINTS = {
    "living_sofa": (0.80, 0.78, 0.74, 1.0),
    "living_table": (0.62, 0.56, 0.50, 1.0),
    "kitchen_counter": (0.62, 0.58, 0.52, 1.0),
    "bedroom_bed": (0.74, 0.78, 0.86, 1.0),
    "living_table_vase": (0.30, 0.58, 0.54, 1.0),
    "kitchen_dining_chair_east": (0.55, 0.66, 0.72, 1.0),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_texture_path(dae_path: Path, image_path: str) -> Path:
    """Resolve a Collada image locally, including Windows separators."""
    normalized = Path(*PureWindowsPath(image_path.replace("/", "\\")).parts)
    candidate = (dae_path.parent / normalized).resolve()
    model_root = dae_path.parents[1].resolve()
    checkout_root = next(
        (parent.resolve() for parent in dae_path.parents if (parent / ".git").is_dir()),
        model_root,
    )
    if candidate.is_file() and candidate.is_relative_to(checkout_root):
        return candidate
    # Some exporters record the basename with an obsolete directory prefix.
    matches = sorted(checkout_root.glob(f"**/{normalized.name}"))
    if len(matches) == 1:
        return matches[0].resolve()
    raise FileNotFoundError(
        f"texture {image_path!r} referenced by {dae_path} was not found inside {checkout_root}"
    )


def _material_texture(primitive, dae_path: Path) -> Path | None:
    diffuse = getattr(getattr(primitive, "material", None), "effect", None)
    diffuse = getattr(diffuse, "diffuse", None)
    image = getattr(getattr(getattr(diffuse, "sampler", None), "surface", None), "image", None)
    return _safe_texture_path(dae_path, image.path) if image is not None else None


def convert_dae(dae_path: Path, output_dir: Path) -> dict:
    """Convert a Collada scene to deterministic, MuJoCo-friendly OBJ parts."""
    try:
        import collada
    except ImportError as exc:  # pragma: no cover - exercised by packaging, not unit tests
        raise RuntimeError(
            "furnished-home preparation requires pycollada; install .[visual]"
        ) from exc

    if not dae_path.is_file():
        raise FileNotFoundError(f"required furnished-home mesh is missing: {dae_path}")
    try:
        document = collada.Collada(str(dae_path))
    except Exception as exc:
        raise ValueError(f"invalid Collada mesh {dae_path}: {exc}") from exc
    if document.scene is None:
        raise ValueError(f"invalid Collada mesh {dae_path}: no visual scene")
    unit = float(document.assetInfo.unitmeter or 1.0)
    if not math.isfinite(unit) or unit <= 0:
        raise ValueError(f"invalid Collada unit in {dae_path}: {unit!r}")

    output_dir.mkdir(parents=True, exist_ok=True)
    parts: list[dict] = []
    all_vertices: list[np.ndarray] = []
    part_index = 0
    for geometry in document.scene.objects("geometry"):
        transform = np.asarray(geometry.matrix, dtype=np.float64)
        if transform.shape != (4, 4) or not np.isfinite(transform).all():
            raise ValueError(f"invalid scene-node transform in {dae_path}")
        for primitive in geometry.primitives():
            # Bound primitives already expose vertices with the complete visual-
            # scene node transform applied. Applying geometry.matrix again would
            # double transforms on nested/translated nodes.
            triangles = primitive.triangleset() if hasattr(primitive, "triangleset") else primitive
            vertices = np.asarray(triangles.vertex, dtype=np.float64)
            transformed = vertices * unit
            faces = np.asarray(triangles.vertex_index, dtype=np.int64)
            if not len(faces):
                continue
            if (
                not np.isfinite(transformed).all()
                or faces.min() < 0
                or faces.max() >= len(vertices)
            ):
                raise ValueError(f"invalid finite geometry or indices in {dae_path}")
            texcoords = None
            texfaces = None
            if getattr(triangles, "texcoordset", None):
                texcoords = np.asarray(triangles.texcoordset[0], dtype=np.float64)
                texfaces = np.asarray(triangles.texcoord_indexset[0], dtype=np.int64)
                if (
                    not np.isfinite(texcoords).all()
                    or texfaces.shape != faces.shape
                    or not len(texcoords)
                    or texfaces.min() < 0
                    or texfaces.max() >= len(texcoords)
                ):
                    raise ValueError(f"invalid UV coordinates in {dae_path}")

            stem = f"part-{part_index:03d}"
            obj_path = output_dir / f"{stem}.obj"
            lines = ["# Prepared from AWS RoboMaker Small House; see pack LICENSE\n"]
            lines.extend(f"v {x:.9g} {y:.9g} {z:.9g}\n" for x, y, z in transformed)
            if texcoords is not None:
                lines.extend(f"vt {u:.9g} {v:.9g}\n" for u, v in texcoords)
            for face_index, face in enumerate(faces):
                if texfaces is None:
                    refs = [str(int(index) + 1) for index in face]
                else:
                    refs = [
                        f"{int(index) + 1}/{int(uv) + 1}"
                        for index, uv in zip(face, texfaces[face_index], strict=True)
                    ]
                lines.append("f " + " ".join(refs) + "\n")
            obj_path.write_text("".join(lines), encoding="utf-8", newline="\n")

            texture = _material_texture(primitive, dae_path)
            texture_name = None
            if texture is not None:
                texture_name = f"{stem}.png"
                with Image.open(texture) as image:
                    image.convert("RGB").save(output_dir / texture_name, format="PNG")
            effect = getattr(getattr(primitive, "material", None), "effect", None)
            rgba = getattr(effect, "diffuse", (0.7, 0.7, 0.7, 1.0))
            if not isinstance(rgba, tuple):
                rgba = (1.0, 1.0, 1.0, 1.0)
            parts.append(
                {
                    "mesh": obj_path.name,
                    "meshSHA256": _sha256(obj_path),
                    "texture": texture_name,
                    "textureSHA256": _sha256(output_dir / texture_name) if texture_name else None,
                    "rgba": [float(value) for value in rgba],
                }
            )
            all_vertices.append(transformed)
            part_index += 1
    if not parts:
        raise ValueError(f"invalid Collada mesh {dae_path}: no triangle geometry")
    bounds = np.ptp(np.vstack(all_vertices), axis=0)
    if np.any(bounds <= 0) or not np.isfinite(bounds).all():
        raise ValueError(f"invalid zero-volume geometry in {dae_path}")
    vertices = np.vstack(all_vertices)
    center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2.0
    return {
        "parts": parts,
        "sourceBoundsM": bounds.tolist(),
        "sourceCenterM": center.tolist(),
    }


def _copy_surface_texture(source: Path, model: str, dae_name: str, output: Path) -> dict:
    import collada

    dae_path = source / "models" / model / "meshes" / dae_name
    if not dae_path.is_file():
        raise FileNotFoundError(f"required furnished-home surface mesh is missing: {dae_path}")
    document = collada.Collada(str(dae_path))
    try:
        primitive = next(next(document.scene.objects("geometry")).primitives())
    except (AttributeError, StopIteration) as exc:
        raise ValueError(f"surface asset contains no material geometry: {dae_path}") from exc
    texture = _material_texture(primitive, dae_path)
    if texture is None:
        raise ValueError(f"surface asset contains no diffuse texture: {dae_path}")
    filename = f"surface-{len(list(output.glob('surface-*'))):02d}.png"
    destination = output / filename
    with Image.open(texture) as image:
        image.convert("RGB").save(destination, format="PNG")
    return {
        "file": filename,
        "sha256": _sha256(destination),
        "source": str(texture.relative_to(source)),
        "sourceSHA256": _sha256(texture),
    }


def prepare(source: Path, output: Path) -> dict:
    """Create a reproducible furnished-home pack from a pinned clean checkout."""
    source = source.resolve()
    output = output.resolve()
    if output in {ROOT.resolve(), source, source.parent} or source.is_relative_to(output):
        raise ValueError(f"unsafe furnished-home output directory: {output}")
    staging = output.with_name(output.name + ".tmp")
    if staging.exists():
        raise FileExistsError(
            f"temporary furnished-home directory already exists: {staging}; inspect and remove it"
        )
    if output.exists():
        try:
            existing = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"refusing to replace unrelated output directory without a valid manifest: {output}"
            ) from exc
        if existing.get("schemaVersion") != 1 or existing.get("repository") != SOURCE_URL:
            raise ValueError(f"refusing to replace unrelated furnished-home directory: {output}")
    if not (source / ".git").is_dir():
        raise FileNotFoundError(
            f"AWS Small House pack is absent at {source}; run scripts/prepare_home_world.py first"
        )
    import subprocess

    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain"], text=True
    ).strip()
    if revision != SOURCE_REVISION or dirty:
        raise ValueError("AWS Small House checkout must be clean and pinned to " + SOURCE_REVISION)
    staging.mkdir(parents=True)
    furnishings = []
    try:
        for body, model, dae_name, target_size, euler in FURNISHINGS:
            dae_path = source / "models" / model / "meshes" / dae_name
            converted = convert_dae(dae_path, staging / body)
            furnishings.append(
                {
                    "body": body,
                    "targetSizeM": list(target_size),
                    "eulerDeg": list(euler) if euler else None,
                    "tintRGBA": list(TINTS.get(body, (1.0, 1.0, 1.0, 1.0))),
                    "source": str(dae_path.relative_to(source)),
                    "sourceSHA256": _sha256(dae_path),
                    **converted,
                }
            )
        decorations = []
        for body, model, dae_name, target_size, position, collider in DECORATIONS:
            dae_path = source / "models" / model / "meshes" / dae_name
            converted = convert_dae(dae_path, staging / body)
            decorations.append(
                {
                    "body": body,
                    "targetSizeM": list(target_size),
                    "positionM": list(position),
                    "collisionHalfExtentM": list(collider) if collider else None,
                    "tintRGBA": list(TINTS.get(body, (1.0, 1.0, 1.0, 1.0))),
                    "source": str(dae_path.relative_to(source)),
                    "sourceSHA256": _sha256(dae_path),
                    **converted,
                }
            )
        decoration_by_body = {item["body"]: item for item in decorations}
        task_assets = {
            "tableware": {
                **decoration_by_body["kitchen_tableware"],
                "directory": "kitchen_tableware",
                "observeOnly": True,
            },
            "vase": {
                **decoration_by_body["living_table_vase"],
                "directory": "living_table_vase",
                "observeOnly": True,
            },
        }
        for item in task_assets.values():
            for key in ("body", "targetSizeM", "positionM", "collisionHalfExtentM"):
                item.pop(key, None)
        shutil.copyfile(source / "LICENSE", staging / "LICENSE")
        surfaces = {
            material: _copy_surface_texture(source, model, dae_name, staging)
            for material, model, dae_name in SURFACES
        }
        tile_path = staging / "surface-tile.png"
        tile = Image.new("RGB", (128, 128), (181, 194, 195))
        pixels = tile.load()
        for y in range(128):
            for x in range(128):
                if x < 5 or y < 5:
                    pixels[x, y] = (84, 92, 94)
                elif (x + y) % 17 == 0:
                    pixels[x, y] = (170, 186, 188)
        tile.save(tile_path, format="PNG")
        surfaces["home_tile_material"] = {
            "file": tile_path.name,
            "sha256": _sha256(tile_path),
            "source": "procedural ceramic tile with grout",
            "sourceSHA256": None,
        }
        manifest = {
            "schemaVersion": 1,
            "repository": SOURCE_URL,
            "revision": revision,
            "license": "MIT-0; see LICENSE",
            "artificialCommissionedLayout": True,
            "furnishings": furnishings,
            "decorations": decorations,
            "taskAssets": task_assets,
            "surfaces": surfaces,
        }
        encoded = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        (staging / "manifest.json").write_text(encoded, encoding="utf-8")
        if output.exists():
            shutil.rmtree(output)
        staging.rename(output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=ROOT / "artifacts/sim-assets/aws-small-house"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/sim-assets/furnished-home")
    args = parser.parse_args()
    manifest = prepare(args.source.resolve(), args.output.resolve())
    print(json.dumps({"pack": str(args.output.resolve()), **manifest}, indent=2))


if __name__ == "__main__":
    main()
