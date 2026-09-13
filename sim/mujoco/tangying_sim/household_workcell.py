"""Physical household workcell and its measured-shape detector catalogue.

Placements initialize a new simulation only. They never enter perception.
The mug uses a hollow rendered surface and a conservative grasp collision proxy.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import mujoco
import numpy as np

from .home_scene import (
    HOME_TASK_BIN_HALF_EXTENT,
    HOME_TASK_BIN_POSITION,
    HOME_TASK_BIN_SHELF_CENTER,
    HOME_TASK_BIN_SHELF_HALF_EXTENT,
    HOME_TASK_BIN_SURFACE_HALF_EXTENT,
    HOME_TASK_BIN_WALL_THICKNESS,
    HOME_TASK_CUP_POSITION,
    HOME_TASK_TABLE_CENTER,
    HOME_TASK_TABLE_HALF_EXTENT,
)

HOUSEHOLD_OBJECTS = (
    ("ceramic-mug", "ceramic_mug", "ceramic_mug_free", "cup", ""),
    ("dinnerware", "dinnerware", "dinnerware_free", "plate", ""),
    ("ceramic-vase", "ceramic_vase", "ceramic_vase_free", "vase", ""),
)
HOUSEHOLD_PLACEMENTS = {
    "ceramic_mug_free": HOME_TASK_CUP_POSITION,
    "dinnerware_free": (2.85, 3.98, .824),
    "ceramic_vase_free": (2.82, 3.66, .85),
}
HOUSEHOLD_DIMENSIONS = {
    "ceramic-mug": (.09, .09, .12),
    # Both AWS meshes preserve their original proportions at uniform 0.8 scale.
    "dinnerware": (.2769131927, .1595688553, .1876213837),
    "ceramic-vase": (.0716437912, .0971251488, .2413838895),
}
HOUSEHOLD_REVISION = "household-ceramic-geometry-v1"
# Action references only: decorative dinnerware/vase are not supported picks.
# No pose or colour is inferred from model initialization or visual materials.
HOUSEHOLD_ACTION_CATALOG = (
    {"id": "ceramic-mug", "category": "cup", "attributes": {},
     "confidence": 1., "workArea": "kitchen"},
    {"id": "kitchen-tray", "category": "storage_bin", "attributes": {},
     "confidence": 1., "workArea": "kitchen"},
)


def _mug_mesh(spec):
    # Closed wall cross-section swept around the vertical axis. The inner
    # bottom is 10 mm above the outside bottom; the top remains visibly open.
    profile = [(0, -.06), (.045, -.06), (.045, .06), (.038, .06),
               (.038, -.05), (0, -.05)]
    count = 64
    vertices = []
    for radius, z in profile:
        vertices.extend((radius * math.cos(2 * math.pi * i / count),
                         radius * math.sin(2 * math.pi * i / count), z)
                        for i in range(count))
    faces = []
    for ring in range(len(profile) - 1):
        for i in range(count):
            a, b = ring * count + i, ring * count + (i + 1) % count
            faces.extend(((a, b, b + count), (a, b + count, a + count)))
    spec.add_mesh(name="household_mug_shell", uservert=np.asarray(vertices).ravel().tolist(),
                  userface=np.asarray(faces).ravel().tolist())


def build_household_workcell(spec, pack_dir: str | Path):
    from .furnished_home import _validated_file, register_task_assets

    pack_dir = Path(pack_dir)
    manifest = json.loads((pack_dir / "manifest.json").read_text())
    surface = manifest["surfaces"]["home_floor_material"]
    wood_file = _validated_file(pack_dir, surface["file"], surface["sha256"])
    spec.add_texture(name="household_wood_texture", type=mujoco.mjtTexture.mjTEXTURE_2D,
                     file=str(wood_file))
    textures = [""]*10
    textures[int(mujoco.mjtTextureRole.mjTEXROLE_RGB)] = "household_wood_texture"
    spec.add_material(name="household_wood", textures=textures, texrepeat=[1, 1],
                      rgba=[.58, .43, .28, 1], specular=.12, shininess=.15)

    island = spec.body("kitchen_island")
    if island is not None:
        spec.delete(island)
    table = spec.worldbody.add_body(name="home_task_table", pos=list(HOME_TASK_TABLE_CENTER))
    table.add_geom(name="home_task_table_top", type=mujoco.mjtGeom.mjGEOM_BOX,
                   size=list(HOME_TASK_TABLE_HALF_EXTENT), material="household_wood")
    table.add_geom(name="home_task_bin_shelf", type=mujoco.mjtGeom.mjGEOM_BOX,
                   pos=(np.asarray(HOME_TASK_BIN_SHELF_CENTER) - HOME_TASK_TABLE_CENTER).tolist(),
                   size=list(HOME_TASK_BIN_SHELF_HALF_EXTENT), material="household_wood")
    # Visible cabinet fronts, fine panel gaps and brushed handles give the task
    # station the same domestic scale as the surrounding furnished kitchen.
    for index, x in enumerate((-.29, 0, .29)):
        table.add_geom(name=f"household_cabinet_panel_{index}",
                       type=mujoco.mjtGeom.mjGEOM_BOX, pos=[x, -.398, -.01],
                       size=[.139, .006, .275], rgba=[.59, .52, .41, 1],
                       contype=0, conaffinity=0)
        table.add_geom(name=f"household_cabinet_handle_{index}",
                       type=mujoco.mjtGeom.mjGEOM_CAPSULE,
                       fromto=[x-.045, -.413, .15, x+.045, -.413, .15],
                       size=[.004], rgba=[.34, .35, .34, 1], contype=0, conaffinity=0)
    spec.add_material(name="household_ceramic", rgba=[.84, .80, .71, 1],
                      specular=.35, shininess=.35)
    spec.add_material(name="household_steel", rgba=[.48, .49, .47, 1],
                      specular=.45, shininess=.40)
    _mug_mesh(spec)
    mug = spec.worldbody.add_body(name="ceramic_mug", pos=list(HOME_TASK_CUP_POSITION))
    mug.add_freejoint(name="ceramic_mug_free")
    mug.add_geom(name="ceramic_mug_collision", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                 size=[.045, .06], rgba=[0, 0, 0, 0], mass=.08,
                 friction=[1, .01, .001])
    mug.add_geom(name="ceramic_mug_shell", type=mujoco.mjtGeom.mjGEOM_MESH,
                 meshname="household_mug_shell", material="household_ceramic",
                 mass=0, contype=0, conaffinity=0)
    # Handle faces away from the commissioned grasp approach; it is visual and
    # does not change the validated collision/attachment envelope.
    for index in range(20):
        angles = [2*math.pi*index/20, 2*math.pi*(index+1)/20]
        ends = [[.048 + .024*math.cos(a), 0, .029*math.sin(a)] for a in angles]
        mug.add_geom(name=f"ceramic_mug_handle_{index}",
                     type=mujoco.mjtGeom.mjGEOM_CAPSULE, fromto=[*ends[0], *ends[1]],
                     size=[.0045], material="household_ceramic", mass=0,
                     contype=0, conaffinity=0)
    tray = spec.worldbody.add_body(name="kitchen_tray", pos=list(HOME_TASK_BIN_POSITION))
    tray.add_geom(name="kitchen_tray_surface", type=mujoco.mjtGeom.mjGEOM_BOX,
                  size=list(HOME_TASK_BIN_SURFACE_HALF_EXTENT), material="household_steel",
                  friction=[1, .01, .001])
    tray.add_geom(name="kitchen_tray_catch", type=mujoco.mjtGeom.mjGEOM_BOX,
                  pos=[0, 0, -.035], size=[*HOME_TASK_BIN_SURFACE_HALF_EXTENT[:2], .02],
                  rgba=[0, 0, 0, 0], friction=[1, .01, .001])
    x = HOME_TASK_BIN_HALF_EXTENT[0] - HOME_TASK_BIN_WALL_THICKNESS
    y = HOME_TASK_BIN_HALF_EXTENT[1] - HOME_TASK_BIN_WALL_THICKNESS
    for index, (pos, size) in enumerate((
        ([-x, 0, .06], [.005, .075, .06]), ([x, 0, .06], [.005, .075, .06]),
        ([0, -y, .06], [.07, .005, .06]), ([0, y, .06], [.07, .005, .06]),
    )):
        tray.add_geom(name=f"kitchen_tray_wall_{index}", type=mujoco.mjtGeom.mjGEOM_BOX,
                      pos=pos, size=size, material="household_steel",
                      friction=[1, .01, .001])
    assets = register_task_assets(spec, pack_dir, {
        "tableware": HOUSEHOLD_DIMENSIONS["dinnerware"],
        "vase": HOUSEHOLD_DIMENSIONS["ceramic-vase"],
    })
    for asset_name, entity_id, body_name, joint_name, _category, _colour in (
        ("tableware", *HOUSEHOLD_OBJECTS[1]), ("vase", *HOUSEHOLD_OBJECTS[2]),
    ):
        asset = assets[asset_name]
        body = spec.worldbody.add_body(name=body_name, pos=list(HOUSEHOLD_PLACEMENTS[joint_name]))
        body.add_freejoint(name=joint_name)
        dimensions = HOUSEHOLD_DIMENSIONS[entity_id]
        body.add_geom(name=f"{body_name}_collision", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                      size=[min(dimensions[:2])/2, dimensions[2]/2], mass=.15,
                      rgba=[0, 0, 0, 0], friction=[1, .01, .001])
        for index, part in enumerate(asset["parts"]):
            body.add_geom(name=f"{body_name}_visual_{index}",
                          type=mujoco.mjtGeom.mjGEOM_MESH, meshname=part["mesh"],
                          material=part["material"], pos=asset["offset"],
                          mass=0, contype=0, conaffinity=0)
