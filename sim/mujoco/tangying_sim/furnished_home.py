"""Attach a prepared, licensed furniture pack to the commissioned home spec."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import mujoco
import numpy as np


def _validated_file(pack_dir: Path, relative: str, expected_hash: str) -> Path:
    if not isinstance(relative, str) or not isinstance(expected_hash, str):
        raise TypeError("furnished-home pack file path and SHA-256 must be strings")
    path = (pack_dir / relative).resolve()
    if not path.is_relative_to(pack_dir.resolve()) or not path.is_file():
        raise FileNotFoundError(f"furnished-home pack file is missing: {relative}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected_hash:
        raise ValueError(
            f"furnished-home pack file has invalid SHA-256: {relative} "
            f"(expected {expected_hash}, got {actual})"
        )
    return path


def _vector(value, length: int, field: str, body_name: str, *, positive: bool = False):
    try:
        vector = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field} for {body_name!r}") from exc
    if vector.shape != (length,) or not np.isfinite(vector).all():
        raise ValueError(f"invalid finite {field} for {body_name!r}")
    if positive and np.any(vector <= 0):
        raise ValueError(f"invalid positive {field} for {body_name!r}")
    return vector


def _visual_box(spec, name, pos, size, rgba, material=None):
    body = spec.worldbody.add_body(name=name, pos=pos)
    body.add_geom(
        name=f"{name}_visual",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=size,
        rgba=rgba,
        material=material,
        contype=0,
        conaffinity=0,
        group=2,
    )
    return body


def _add_architectural_finishes(spec):
    """Add thin rendered finishes without changing commissioned collisions."""
    tile_material = (
        "home_tile_material" if spec.material("home_tile_material") is not None else None
    )
    for room_name in ("living_room", "home_corridor", "kitchen", "bedroom", "bathroom"):
        room = spec.body(room_name)
        if room is not None:
            next(iter(room.geoms)).rgba = [0.0, 0.0, 0.0, 0.0]

    for body_name, pos, size in (
        ("kitchen_tiles", [2.65, 4.75, 0.012], [1.77, 3.65, 0.006]),
        ("bathroom_tiles", [-2.65, 6.825, 0.012], [1.77, 1.60, 0.006]),
    ):
        body = spec.body(body_name)
        if body is not None:
            body.pos = pos
            geom = next(iter(body.geoms))
            geom.size = size
            if tile_material:
                geom.material = tile_material
            geom.rgba = [0.82, 0.84, 0.82, 1.0]

    # Low-reflectance ceramic backsplash and bathroom wall tile panels.
    for name, pos, size in (
        ("kitchen_wall_tile_north", [2.65, 8.405, 0.80], [1.72, 0.012, 0.80]),
        ("kitchen_wall_tile_east", [4.405, 5.55, 0.80], [0.012, 2.75, 0.80]),
        ("bathroom_wall_tile_north", [-2.65, 8.405, 0.95], [1.72, 0.012, 0.95]),
        ("bathroom_wall_tile_west", [-4.405, 6.82, 0.95], [0.012, 1.55, 0.95]),
    ):
        _visual_box(spec, name, pos, size, [0.76, 0.79, 0.78, 1.0], tile_material)

    # Explicit grout strips remain readable in top-down RGB even after texture filtering.
    grout = [0.30, 0.31, 0.30, 1.0]
    for prefix, x_low, x_high, y_low, y_high in (
        ("kitchen", 0.88, 4.42, 1.10, 8.40),
        ("bathroom", -4.42, -0.88, 5.23, 8.40),
    ):
        for index, x in enumerate(np.arange(x_low, x_high + 0.01, 0.5)):
            _visual_box(
                spec,
                f"{prefix}_floor_grout_x_{index}",
                [float(x), (y_low + y_high) / 2, 0.020],
                [0.006, (y_high - y_low) / 2, 0.002],
                grout,
            )
        for index, y in enumerate(np.arange(y_low, y_high + 0.01, 0.5)):
            _visual_box(
                spec,
                f"{prefix}_floor_grout_y_{index}",
                [(x_low + x_high) / 2, float(y), 0.020],
                [(x_high - x_low) / 2, 0.006, 0.002],
                grout,
            )

    for prefix, axis, fixed, low, high, height in (
        ("kitchen_north", "x", 8.391, 0.93, 4.37, 1.55),
        ("kitchen_east", "y", 4.391, 2.80, 8.30, 1.55),
        ("bathroom_north", "x", 8.391, -4.37, -0.93, 1.85),
        ("bathroom_west", "y", -4.391, 5.27, 8.37, 1.85),
    ):
        for index, value in enumerate(np.arange(low, high + 0.01, 0.45)):
            pos = (
                [float(value), fixed, height / 2]
                if axis == "x"
                else [fixed, float(value), height / 2]
            )
            size = [0.006, 0.004, height / 2] if axis == "x" else [0.004, 0.006, height / 2]
            _visual_box(spec, f"{prefix}_grout_v_{index}", pos, size, grout)
        for index, z in enumerate(np.arange(0.35, height, 0.35)):
            pos = (
                [(low + high) / 2, fixed, float(z)]
                if axis == "x"
                else [fixed, (low + high) / 2, float(z)]
            )
            size = (
                [(high - low) / 2, 0.004, 0.006]
                if axis == "x"
                else [0.004, (high - low) / 2, 0.006]
            )
            _visual_box(spec, f"{prefix}_grout_h_{index}", pos, size, grout)

    # Timber trim sits on the wall faces beside openings, preserving clear width.
    wood = [0.46, 0.30, 0.19, 1.0]
    for name, pos, size in (
        ("living_door_trim_left", [-1.54, 0.90, 1.05], [0.045, 0.025, 1.05]),
        ("living_door_trim_right", [1.54, 0.90, 1.05], [0.045, 0.025, 1.05]),
        ("living_door_trim_top", [0.0, 0.90, 2.16], [1.585, 0.025, 0.055]),
        ("bed_bath_trim_left", [-2.695, 5.06, 1.05], [0.045, 0.025, 1.05]),
        ("bed_bath_trim_right", [-1.405, 5.06, 1.05], [0.045, 0.025, 1.05]),
        ("bed_bath_trim_top", [-2.05, 5.06, 2.16], [0.605, 0.025, 0.055]),
    ):
        _visual_box(spec, name, pos, size, wood)

    # Opaque-wall window treatments provide domestic scale without a false exterior view.
    glass = [0.24, 0.38, 0.46, 0.55]
    frame = [0.58, 0.55, 0.50, 1.0]
    _visual_box(spec, "living_window_glass", [-2.55, -2.405, 1.38], [0.90, 0.012, 0.55], glass)
    _visual_box(spec, "living_window_top", [-2.55, -2.385, 1.96], [0.97, 0.025, 0.035], frame)
    _visual_box(spec, "living_window_bottom", [-2.55, -2.385, 0.80], [0.97, 0.025, 0.035], frame)
    for suffix, x in (("left", -3.48), ("center", -2.55), ("right", -1.62)):
        _visual_box(spec, f"living_window_{suffix}", [x, -2.385, 1.38], [0.035, 0.025, 0.55], frame)
    curtain = [0.55, 0.48, 0.42, 0.88]
    _visual_box(spec, "living_curtain_left", [-3.62, -2.36, 1.35], [0.12, 0.035, 0.82], curtain)
    _visual_box(spec, "living_curtain_right", [-1.48, -2.36, 1.35], [0.12, 0.035, 0.82], curtain)


def _preflight(spec: mujoco.MjSpec, pack_dir: Path, manifest: dict):
    """Validate the complete pack without changing the destination spec."""
    surfaces = []
    for material_name, surface in sorted(manifest.get("surfaces", {}).items()):
        if not isinstance(material_name, str) or not isinstance(surface, dict):
            raise TypeError("invalid furnished-home surface entry")
        if material_name != "home_tile_material" and spec.material(material_name) is None:
            raise ValueError(f"furnished-home surface material is missing: {material_name}")
        surfaces.append(
            (material_name, _validated_file(pack_dir, surface.get("file"), surface.get("sha256")))
        )

    raw_items = [*(item | {"existing": True} for item in manifest["furnishings"])]
    raw_items.extend(item | {"existing": False} for item in manifest.get("decorations", []))
    prepared = []
    seen_names: set[str] = set()
    for item in raw_items:
        body_name = item.get("body")
        if not isinstance(body_name, str) or not body_name or body_name in seen_names:
            raise ValueError(f"invalid or duplicate furnishing body name: {body_name!r}")
        seen_names.add(body_name)
        body = spec.body(body_name)
        if item["existing"] and body is None:
            raise ValueError(f"furnished-home target body is missing from scene: {body_name!r}")
        if not item["existing"] and body is not None:
            raise ValueError(f"furnished-home decoration body already exists: {body_name!r}")
        position = None
        if not item["existing"]:
            position = _vector(item.get("positionM"), 3, "positionM", body_name)
        target = _vector(item.get("targetSizeM"), 3, "targetSizeM", body_name, positive=True)
        source = _vector(item.get("sourceBoundsM"), 3, "sourceBoundsM", body_name, positive=True)
        center = _vector(item.get("sourceCenterM"), 3, "sourceCenterM", body_name)
        scale = target / source
        if not np.isfinite(scale).all():
            raise ValueError(f"invalid finite derived scale for {body_name!r}")
        euler = _vector(item.get("eulerDeg") or (0.0, 0.0, 0.0), 3, "eulerDeg", body_name)
        quat = np.empty(4, dtype=float)
        mujoco.mju_euler2Quat(quat, np.radians(euler), "xyz")
        rotation = np.empty(9, dtype=float)
        mujoco.mju_quat2Mat(rotation, quat)
        rotation = rotation.reshape(3, 3)
        collision = None
        if item.get("collisionHalfExtentM") is not None:
            collision = _vector(
                item["collisionHalfExtentM"],
                3,
                "collisionHalfExtentM",
                body_name,
                positive=True,
            )
        tint = _vector(item.get("tintRGBA", (1, 1, 1, 1)), 4, "tintRGBA", body_name)
        if np.any(tint < 0) or np.any(tint > 1):
            raise ValueError(f"invalid tintRGBA range for {body_name!r}")
        parts = []
        if not isinstance(item.get("parts"), list) or not item["parts"]:
            raise ValueError(f"furnished-home target {body_name!r} has no visual parts")
        for part in item["parts"]:
            if not isinstance(part, dict):
                raise TypeError(f"invalid visual part for {body_name!r}")
            mesh_path = _validated_file(
                pack_dir, f"{body_name}/{part.get('mesh')}", part.get("meshSHA256")
            )
            texture_path = None
            if part.get("texture") is not None:
                texture_path = _validated_file(
                    pack_dir,
                    f"{body_name}/{part.get('texture')}",
                    part.get("textureSHA256"),
                )
            rgba = _vector(part.get("rgba", (1, 1, 1, 1)), 4, "part rgba", body_name)
            if np.any(rgba < 0) or np.any(rgba > 1):
                raise ValueError(f"invalid part rgba range for {body_name!r}")
            parts.append((mesh_path, texture_path, (rgba * tint).tolist()))
        prepared.append(
            {
                "name": body_name,
                "existing": item["existing"],
                "body": body,
                "position": position,
                "scale": scale,
                "quat": quat,
                "offset": -rotation @ (center * scale),
                "collision": collision,
                "parts": parts,
            }
        )
    return surfaces, prepared


def register_task_assets(
    spec: mujoco.MjSpec,
    pack_dir: str | Path,
    task_sizes: dict[str, tuple[float, float, float]],
) -> dict:
    """Register validated observe-only household meshes at requested dimensions.

    Returned parts can be attached to caller-owned bodies. The caller retains
    ownership of task semantics, joints, collision shapes, and placement.
    """
    pack_dir = Path(pack_dir).resolve()
    manifest_path = pack_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"furnished-home pack is absent at {pack_dir}; run "
            "scripts/prepare_furnished_home.py first"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid furnished-home manifest {manifest_path}: {exc}") from exc
    catalog = manifest.get("taskAssets")
    if not isinstance(catalog, dict):
        raise TypeError("furnished-home manifest has no taskAssets catalog")
    unknown = sorted(set(task_sizes) - set(catalog))
    if unknown:
        raise ValueError(f"unknown furnished-home task assets: {unknown}")

    prepared = []
    for slug, requested_size in task_sizes.items():
        asset = catalog[slug]
        if not isinstance(asset, dict) or asset.get("observeOnly") is not True:
            raise ValueError(f"invalid observe-only task asset {slug!r}")
        target = _vector(requested_size, 3, "task size", slug, positive=True)
        source = _vector(asset.get("sourceBoundsM"), 3, "sourceBoundsM", slug, positive=True)
        center = _vector(asset.get("sourceCenterM"), 3, "sourceCenterM", slug)
        tint = _vector(asset.get("tintRGBA", (1, 1, 1, 1)), 4, "tintRGBA", slug)
        if np.any(tint < 0) or np.any(tint > 1):
            raise ValueError(f"invalid tintRGBA range for {slug!r}")
        scale = target / source
        if not np.isfinite(scale).all():
            raise ValueError(f"invalid finite derived scale for {slug!r}")
        directory = asset.get("directory")
        if not isinstance(directory, str):
            raise TypeError(f"invalid task asset directory for {slug!r}")
        parts = []
        for part in asset.get("parts", []):
            mesh_path = _validated_file(
                pack_dir, f"{directory}/{part.get('mesh')}", part.get("meshSHA256")
            )
            texture_path = None
            if part.get("texture") is not None:
                texture_path = _validated_file(
                    pack_dir,
                    f"{directory}/{part.get('texture')}",
                    part.get("textureSHA256"),
                )
            rgba = _vector(part.get("rgba", (1, 1, 1, 1)), 4, "part rgba", slug)
            if np.any(rgba < 0) or np.any(rgba > 1):
                raise ValueError(f"invalid part rgba range for {slug!r}")
            parts.append((mesh_path, texture_path, (rgba * tint).tolist()))
        if not parts:
            raise ValueError(f"task asset {slug!r} has no visual parts")
        prepared.append((slug, source, center, scale, parts))

    registry = {}
    for asset_index, (slug, source, center, scale, parts) in enumerate(prepared):
        registered_parts = []
        for part_index, (mesh_path, texture_path, rgba) in enumerate(parts):
            prefix = f"task_asset_{slug}_{asset_index}_{part_index}"
            texture_name = None
            if texture_path is not None:
                texture_name = f"{prefix}_texture"
                spec.add_texture(
                    name=texture_name,
                    type=mujoco.mjtTexture.mjTEXTURE_2D,
                    file=str(texture_path),
                )
            slots = [""] * 10
            if texture_name:
                slots[int(mujoco.mjtTextureRole.mjTEXROLE_RGB)] = texture_name
            material_name = f"{prefix}_material"
            spec.add_material(name=material_name, textures=slots, rgba=rgba, roughness=0.7)
            mesh_name = f"{prefix}_mesh"
            spec.add_mesh(
                name=mesh_name,
                file=str(mesh_path),
                scale=scale.tolist(),
                inertia=mujoco.mjtMeshInertia.mjMESH_INERTIA_SHELL,
            )
            registered_parts.append({"mesh": mesh_name, "material": material_name})
        registry[slug] = {
            "parts": registered_parts,
            "offset": (-center * scale).tolist(),
            "scale": scale.tolist(),
            "sourceBoundsM": source.tolist(),
            "observeOnly": True,
        }
    return registry


def apply_furnished_home(spec: mujoco.MjSpec, pack_dir: str | Path) -> dict:
    """Replace home furniture visuals while retaining their collision proxies.

    The caller applies this after optional ``home_task`` fixtures, so task geometry
    and object colours are untouched. Missing or corrupt packs fail visibly.
    """
    pack_dir = Path(pack_dir).resolve()
    manifest_path = pack_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"furnished-home pack is absent at {pack_dir}; run "
            "scripts/prepare_furnished_home.py first"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid furnished-home manifest {manifest_path}: {exc}") from exc
    if manifest.get("schemaVersion") != 1 or not manifest.get("furnishings"):
        raise ValueError(f"invalid furnished-home manifest schema in {manifest_path}")
    surfaces, items = _preflight(spec, pack_dir, manifest)

    # The base scene uses bright neutral light for schematic boxes. Lower, warm
    # illumination preserves the diffuse colors in the real fabric/wood textures.
    key = spec.light("home_key")
    fill = spec.light("home_fill")
    if key is not None:
        key.diffuse = [0.58, 0.55, 0.50]
        key.specular = [0.12, 0.12, 0.12]
    if fill is not None:
        fill.diffuse = [0.20, 0.23, 0.28]
        fill.specular = [0.05, 0.05, 0.05]
    spec.visual.headlight.ambient = [0.16, 0.15, 0.14]
    spec.visual.headlight.diffuse = [0.28, 0.27, 0.25]
    spec.visual.headlight.specular = [0.05, 0.05, 0.05]
    spec.add_texture(
        name="furnished_sky",
        type=mujoco.mjtTexture.mjTEXTURE_SKYBOX,
        builtin=mujoco.mjtBuiltin.mjBUILTIN_GRADIENT,
        width=512,
        height=512,
        rgb1=[0.32, 0.40, 0.52],
        rgb2=[0.82, 0.86, 0.90],
    )
    floor = spec.geom("home_floor")
    if floor is not None:
        floor.pos = [0.0, 3.0, 0.0]
        floor.size = [4.5, 5.5, 0.1]
    for body_name in ("wall_outer_west", "wall_outer_east", "wall_outer_south", "wall_outer_north"):
        body = spec.body(body_name)
        if body is not None:
            next(iter(body.geoms)).rgba = [0.90, 0.88, 0.84, 1.0]
    for body_name in (
        "wall_living_west",
        "wall_living_east",
        "wall_corridor_kitchen_south",
        "wall_corridor_kitchen_north",
        "wall_corridor_bed_south",
        "wall_corridor_bed_north",
        "wall_bed_bath",
        "wall_bed_bath_east",
    ):
        body = spec.body(body_name)
        if body is not None:
            next(iter(body.geoms)).rgba = [0.88, 0.82, 0.74, 1.0]

    for surface_index, (material_name, texture_path) in enumerate(surfaces):
        texture_name = f"furnished_surface_{surface_index}"
        spec.add_texture(
            name=texture_name,
            type=mujoco.mjtTexture.mjTEXTURE_2D,
            file=str(texture_path),
        )
        slots = [""] * 10
        slots[1] = texture_name
        material = spec.material(material_name)
        if material is None:
            material = spec.add_material(name=material_name, texrepeat=[8.0, 8.0])
        material.textures = slots
        material.rgba = {
            "home_floor_material": [0.64, 0.59, 0.52, 1.0],
            "home_rug_material": [0.66, 0.64, 0.60, 1.0],
            "home_tile_material": [0.78, 0.80, 0.79, 1.0],
        }.get(material_name, [0.82, 0.82, 0.80, 1.0])

    spec.add_material(
        name="furnished_wall_finish",
        rgba=[0.90, 0.87, 0.82, 1.0],
        emission=0.32,
        specular=0.04,
        roughness=0.95,
    )

    _add_architectural_finishes(spec)

    # Thin visual baseboards add wall/floor scale cues without changing collision.
    for body_name in (
        "wall_outer_west",
        "wall_outer_east",
        "wall_outer_south",
        "wall_outer_north",
        "wall_living_west",
        "wall_living_east",
        "wall_corridor_kitchen_south",
        "wall_corridor_kitchen_north",
        "wall_corridor_bed_south",
        "wall_corridor_bed_north",
        "wall_bed_bath",
        "wall_bed_bath_east",
    ):
        wall_body = spec.body(body_name)
        if wall_body is None:
            continue
        wall_geom = next(iter(wall_body.geoms))
        wall_body.add_geom(
            name=f"{body_name}_finish",
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=[float(value) + 0.002 for value in wall_geom.size],
            material="furnished_wall_finish",
            contype=0,
            conaffinity=0,
            group=2,
        )
        wall_body.add_geom(
            name=f"{body_name}_baseboard",
            type=mujoco.mjtGeom.mjGEOM_BOX,
            pos=[0.0, 0.0, -1.12],
            size=[float(wall_geom.size[0]) + 0.015, float(wall_geom.size[1]) + 0.015, 0.08],
            rgba=[0.72, 0.66, 0.57, 1.0],
            contype=0,
            conaffinity=0,
            group=2,
        )

    for item_index, item in enumerate(items):
        body_name = item["name"]
        body = item["body"]
        if not item["existing"]:
            body = spec.worldbody.add_body(name=body_name, pos=item["position"].tolist())
        scale = item["scale"]
        # Keep the original box as the collision proxy and make it invisible.
        if item["existing"]:
            original_geoms = list(body.geoms)
            if len(original_geoms) != 1:
                raise ValueError(f"furnished-home target {body_name!r} must have one collision box")
            collider = original_geoms[0]
            if collider.type != mujoco.mjtGeom.mjGEOM_BOX:
                raise ValueError(f"furnished-home target {body_name!r} collision must be a box")
            collider.rgba = [0.0, 0.0, 0.0, 0.0]
            collider.group = 3
        elif item["collision"] is not None:
            half_extent = item["collision"]
            body.add_geom(
                name=f"{body_name}_collision",
                type=mujoco.mjtGeom.mjGEOM_BOX,
                size=half_extent.tolist(),
                rgba=[0.0, 0.0, 0.0, 0.0],
                contype=1,
                conaffinity=1,
                group=3,
            )

        for part_index, (mesh_path, texture_path, rgba) in enumerate(item["parts"]):
            prefix = f"furnished_{item_index}_{part_index}"
            material_name = f"{prefix}_material"
            texture_name = None
            if texture_path is not None:
                texture_name = f"{prefix}_texture"
                spec.add_texture(
                    name=texture_name,
                    type=mujoco.mjtTexture.mjTEXTURE_2D,
                    file=str(texture_path),
                )
            texture_slots = [""] * 10
            if texture_name:
                texture_slots[int(mujoco.mjtTextureRole.mjTEXROLE_RGB)] = texture_name
            spec.add_material(
                name=material_name,
                textures=texture_slots,
                rgba=rgba,
                specular=0.15,
                shininess=0.1,
            )
            mesh_name = f"{prefix}_mesh"
            spec.add_mesh(
                name=mesh_name,
                file=str(mesh_path),
                scale=scale.tolist(),
                inertia=mujoco.mjtMeshInertia.mjMESH_INERTIA_SHELL,
            )
            body.add_geom(
                name=f"{prefix}_visual",
                type=mujoco.mjtGeom.mjGEOM_MESH,
                meshname=mesh_name,
                material=material_name,
                pos=item["offset"].tolist(),
                quat=item["quat"].tolist(),
                contype=0,
                conaffinity=0,
                group=2,
            )

    return {
        "repository": manifest.get("repository"),
        "revision": manifest.get("revision"),
        "license": manifest.get("license"),
        "manifestSHA256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "artificialCommissionedLayout": True,
    }
