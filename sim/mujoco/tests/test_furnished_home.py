from __future__ import annotations

import hashlib
import json
from pathlib import Path

import mujoco
import numpy as np
import pytest
from PIL import Image
from tangying_sim.furnished_home import apply_furnished_home, register_task_assets
from tangying_sim.home_scene import HOME_ROUTE_EDGES, HOME_TASK_WORK_VOLUME, HOME_WAYPOINTS

from scripts.prepare_furnished_home import DECORATIONS, convert_dae


def _write_synthetic_dae(root: Path) -> Path:
    texture = root / "materials" / "textures" / "fabric.png"
    texture.parent.mkdir(parents=True)
    Image.new("RGB", (2, 2), (80, 120, 160)).save(texture)
    dae = root / "meshes" / "fixture.dae"
    dae.parent.mkdir()
    dae.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset><unit meter="0.01" name="centimeter"/><up_axis>Z_UP</up_axis></asset>
  <library_images><image id="cloth"><init_from>..\\materials\\textures\\fabric.png</init_from></image></library_images>
  <library_effects>
    <effect id="textured-fx"><profile_COMMON><newparam sid="surface"><surface type="2D"><init_from>cloth</init_from></surface></newparam><newparam sid="sampler"><sampler2D><source>surface</source></sampler2D></newparam><technique sid="common"><phong><diffuse><texture texture="sampler" texcoord="CHANNEL0"/></diffuse></phong></technique></profile_COMMON></effect>
    <effect id="plain-fx"><profile_COMMON><technique sid="common"><phong><diffuse><color>0.4 0.3 0.2 1</color></diffuse></phong></technique></profile_COMMON></effect>
  </library_effects>
  <library_materials><material id="textured"><instance_effect url="#textured-fx"/></material><material id="plain"><instance_effect url="#plain-fx"/></material></library_materials>
  <library_geometries>
    <geometry id="front"><mesh><source id="front-pos"><float_array id="front-pos-array" count="12">0 0 0 100 0 0 0 100 0 0 0 10</float_array><technique_common><accessor source="#front-pos-array" count="4" stride="3"><param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source><source id="front-uv"><float_array id="front-uv-array" count="8">0 0 1 0 1 1 0 1</float_array><technique_common><accessor source="#front-uv-array" count="4" stride="2"><param name="S" type="float"/><param name="T" type="float"/></accessor></technique_common></source><vertices id="front-v"><input semantic="POSITION" source="#front-pos"/></vertices><triangles count="4" material="cloth"><input semantic="VERTEX" source="#front-v" offset="0"/><input semantic="TEXCOORD" source="#front-uv" offset="1" set="0"/><p>0 0 1 1 2 2 0 0 3 3 1 1 0 0 2 2 3 3 1 1 3 3 2 2</p></triangles></mesh></geometry>
    <geometry id="back"><mesh><source id="back-pos"><float_array id="back-pos-array" count="12">0 0 90 100 0 100 0 100 100 0 0 100</float_array><technique_common><accessor source="#back-pos-array" count="4" stride="3"><param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source><vertices id="back-v"><input semantic="POSITION" source="#back-pos"/></vertices><triangles count="4" material="wood"><input semantic="VERTEX" source="#back-v" offset="0"/><p>0 1 2 0 3 1 0 2 3 1 3 2</p></triangles></mesh></geometry>
  </library_geometries>
  <library_visual_scenes><visual_scene id="Scene"><node id="offset"><translate>10 20 30</translate><instance_geometry url="#front"><bind_material><technique_common><instance_material symbol="cloth" target="#textured"><bind_vertex_input semantic="CHANNEL0" input_semantic="TEXCOORD" input_set="0"/></instance_material></technique_common></bind_material></instance_geometry><instance_geometry url="#back"><bind_material><technique_common><instance_material symbol="wood" target="#plain"/></technique_common></bind_material></instance_geometry></node></visual_scene></library_visual_scenes>
  <scene><instance_visual_scene url="#Scene"/></scene>
</COLLADA>
""",
        encoding="utf-8",
    )
    return dae


def test_convert_dae_applies_units_node_transforms_uv_materials_and_windows_paths(tmp_path):
    dae = _write_synthetic_dae(tmp_path / "source")
    first = convert_dae(dae, tmp_path / "first")
    second = convert_dae(dae, tmp_path / "second")

    assert first == second
    assert first["sourceBoundsM"] == pytest.approx([1.0, 1.0, 1.0])
    assert first["sourceCenterM"] == pytest.approx([0.6, 0.7, 0.8])
    assert len(first["parts"]) == 2
    assert sum(part["texture"] is not None for part in first["parts"]) == 1
    textured_obj = next(
        (tmp_path / "first" / part["mesh"]) for part in first["parts"] if part["texture"]
    )
    assert "vt 1 0" in textured_obj.read_text()
    assert all(
        hashlib.sha256((tmp_path / "first" / part["mesh"]).read_bytes()).hexdigest()
        == part["meshSHA256"]
        for part in first["parts"]
    )


def test_apply_furnished_home_keeps_collision_proxy_and_adds_visual_mesh(tmp_path):
    dae = _write_synthetic_dae(tmp_path / "source")
    pack = tmp_path / "pack"
    converted = convert_dae(dae, pack / "living_sofa")
    manifest = {
        "schemaVersion": 1,
        "repository": "https://example.invalid/pinned",
        "revision": "abc123",
        "license": "test fixture",
        "artificialCommissionedLayout": True,
        "furnishings": [
            {
                "body": "living_sofa",
                "targetSizeM": [1.1, 2.2, 0.7],
                **converted,
            }
        ],
    }
    pack.mkdir(exist_ok=True)
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    scene = Path(__file__).resolve().parents[1] / "assets" / "xlerobot_home.xml"
    spec = mujoco.MjSpec.from_file(str(scene))

    provenance = apply_furnished_home(spec, pack)
    model = spec.compile()

    assert provenance["revision"] == "abc123"
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "living_sofa")
    collider_id = int(model.body_geomadr[body_id])
    assert model.geom_contype[collider_id] == 1
    assert model.geom_rgba[collider_id, 3] == 0
    visual_ids = [
        index
        for index in range(model.ngeom)
        if (mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, index) or "").startswith(
            "furnished_0_"
        )
    ]
    assert len(visual_ids) == 2
    assert all(model.geom_contype[index] == 0 for index in visual_ids)
    textured_visual = next(index for index in visual_ids if model.geom_matid[index] >= 0)
    material_id = int(model.geom_matid[textured_visual])
    rgb_role = int(mujoco.mjtTextureRole.mjTEXROLE_RGB)
    assert model.mat_texid[material_id, rgb_role] >= 0
    assert np.isfinite(model.mesh_vert).all()
    left_trim = spec.body("bed_bath_trim_left")
    right_trim = spec.body("bed_bath_trim_right")
    assert left_trim.pos[0] + next(iter(left_trim.geoms)).size[0] <= -2.65
    assert right_trim.pos[0] - next(iter(right_trim.geoms)).size[0] >= -1.45


def test_apply_furnished_home_fails_visibly_when_pack_is_absent(tmp_path):
    spec = mujoco.MjSpec()
    with pytest.raises(FileNotFoundError, match="prepare_furnished_home.py"):
        apply_furnished_home(spec, tmp_path / "missing")


def test_rotated_mesh_matches_collision_footprint_and_rotates_center(tmp_path):
    dae = _write_synthetic_dae(tmp_path / "source")
    pack = tmp_path / "pack"
    converted = convert_dae(dae, pack / "kitchen_counter")
    manifest = {
        "schemaVersion": 1,
        "furnishings": [
            {
                "body": "kitchen_counter",
                "targetSizeM": [0.7, 2.5, 0.9],
                "eulerDeg": [0.0, 0.0, 90.0],
                **converted,
            }
        ],
    }
    pack.mkdir(exist_ok=True)
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    scene = Path(__file__).resolve().parents[1] / "assets" / "xlerobot_home.xml"
    spec = mujoco.MjSpec.from_file(str(scene))
    apply_furnished_home(spec, pack)
    body = spec.body("kitchen_counter")
    visual_geoms = [geom for geom in body.geoms if geom.type == mujoco.mjtGeom.mjGEOM_MESH]
    assert visual_geoms[0].quat == pytest.approx([2**-0.5, 0.0, 0.0, 2**-0.5])
    assert visual_geoms[0].pos == pytest.approx([1.75, -0.42, -0.72])

    model = spec.compile()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    vertices = []
    for geom in visual_geoms:
        geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, geom.name)
        mesh_id = int(model.geom_dataid[geom_id])
        start = int(model.mesh_vertadr[mesh_id])
        stop = start + int(model.mesh_vertnum[mesh_id])
        vertices.append(
            model.mesh_vert[start:stop] @ data.geom_xmat[geom_id].reshape(3, 3).T
            + data.geom_xpos[geom_id]
        )
    assert np.ptp(np.vstack(vertices), axis=0) == pytest.approx([2.5, 0.7, 0.9], abs=2e-5)


def test_invalid_numeric_pack_is_rejected_before_spec_mutation(tmp_path):
    dae = _write_synthetic_dae(tmp_path / "source")
    pack = tmp_path / "pack"
    converted = convert_dae(dae, pack / "living_sofa")
    base = {
        "body": "living_sofa",
        "targetSizeM": [1.1, 2.2, 0.7],
        "eulerDeg": [0.0, 0.0, 0.0],
        "tintRGBA": [1.0, 1.0, 1.0, 1.0],
        **converted,
    }
    cases = (
        ("targetSizeM", [float("nan"), 2.2, 0.7]),
        ("sourceBoundsM", [float("inf"), 1.0, 1.0]),
        ("sourceCenterM", [0.0, float("nan"), 0.0]),
        ("eulerDeg", [0.0, 0.0, float("inf")]),
        ("tintRGBA", [1.0, float("nan"), 1.0, 1.0]),
    )
    scene = Path(__file__).resolve().parents[1] / "assets" / "xlerobot_home.xml"
    for field, invalid in cases:
        item = dict(base)
        item[field] = invalid
        (pack / "manifest.json").write_text(
            json.dumps({"schemaVersion": 1, "furnishings": [item]}), encoding="utf-8"
        )
        spec = mujoco.MjSpec.from_file(str(scene))
        original_diffuse = np.array(spec.light("home_key").diffuse)
        with pytest.raises(ValueError, match="invalid"):
            apply_furnished_home(spec, pack)
        assert np.array(spec.light("home_key").diffuse) == pytest.approx(original_diffuse)
        assert len(spec.meshes) == 33

    decor_converted = convert_dae(dae, pack / "bad_decor")
    for field, invalid in (
        ("positionM", [0.0, float("inf"), 0.0]),
        ("collisionHalfExtentM", [0.1, float("nan"), 0.1]),
    ):
        decoration = {
            "body": "bad_decor",
            "targetSizeM": [0.2, 0.2, 0.2],
            "positionM": [3.5, 7.5, 0.1],
            "collisionHalfExtentM": [0.1, 0.1, 0.1],
            **decor_converted,
        }
        decoration[field] = invalid
        (pack / "manifest.json").write_text(
            json.dumps({"schemaVersion": 1, "furnishings": [base], "decorations": [decoration]}),
            encoding="utf-8",
        )
        spec = mujoco.MjSpec.from_file(str(scene))
        with pytest.raises(ValueError, match="invalid"):
            apply_furnished_home(spec, pack)
        assert spec.body("bad_decor") is None
        assert len(spec.meshes) == 33


def test_register_task_assets_returns_scaled_mesh_material_registry(tmp_path):
    dae = _write_synthetic_dae(tmp_path / "source")
    pack = tmp_path / "pack"
    converted = convert_dae(dae, pack / "tableware")
    manifest = {
        "schemaVersion": 1,
        "taskAssets": {
            "tableware": {
                "directory": "tableware",
                "observeOnly": True,
                "tintRGBA": [0.8, 0.75, 0.7, 1.0],
                **converted,
            }
        },
    }
    pack.mkdir(exist_ok=True)
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    spec = mujoco.MjSpec()
    registry = register_task_assets(spec, pack, {"tableware": (0.4, 0.2, 0.1)})
    assert registry["tableware"]["scale"] == pytest.approx([0.4, 0.2, 0.1])
    assert registry["tableware"]["offset"] == pytest.approx([-0.24, -0.14, -0.08])
    assert len(registry["tableware"]["parts"]) == 2
    body = spec.worldbody.add_body(name="tableware")
    for part in registry["tableware"]["parts"]:
        body.add_geom(
            type=mujoco.mjtGeom.mjGEOM_MESH,
            meshname=part["mesh"],
            material=part["material"],
            pos=registry["tableware"]["offset"],
            contype=0,
            conaffinity=0,
        )
    model = spec.compile()
    assert model.nmesh == 2


def test_convert_dae_rejects_out_of_bounds_uv_indices(tmp_path):
    dae = _write_synthetic_dae(tmp_path / "source")
    dae.write_text(dae.read_text().replace("3 3 1 1", "3 9 1 1", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid (Collada mesh|UV coordinates)"):
        convert_dae(dae, tmp_path / "converted")


def test_prepare_refuses_to_replace_unrelated_directory(tmp_path):
    from scripts.prepare_furnished_home import prepare

    unrelated = tmp_path / "existing"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("owned by somebody else")
    with pytest.raises(ValueError, match="unrelated output directory"):
        prepare(tmp_path / "source", unrelated)
    assert (unrelated / "keep.txt").read_text() == "owned by somebody else"


def test_new_colliders_leave_routes_and_task_volume_clear():
    segments = []
    for start, neighbors in HOME_ROUTE_EDGES.items():
        for end in neighbors:
            if start < end:
                segments.append(
                    (np.array(HOME_WAYPOINTS[start][:2]), np.array(HOME_WAYPOINTS[end][:2]))
                )
    for name, _model, _dae, _size, position, collider in DECORATIONS:
        if collider is None:
            continue
        center = np.array(position[:2])
        half = np.array(collider[:2])
        for start, end in segments:
            direction = end - start
            t = np.clip(np.dot(center - start, direction) / np.dot(direction, direction), 0, 1)
            closest = start + t * direction
            clearance = np.linalg.norm(np.maximum(np.abs(center - closest) - half, 0.0))
            assert clearance >= 0.42, f"{name} crowds a commissioned route"
        x_range = HOME_TASK_WORK_VOLUME["x"]
        y_range = HOME_TASK_WORK_VOLUME["y"]
        overlaps_task_xy = (
            center[0] + half[0] >= x_range[0]
            and center[0] - half[0] <= x_range[1]
            and center[1] + half[1] >= y_range[0]
            and center[1] - half[1] <= y_range[1]
        )
        assert not overlaps_task_xy, f"{name} enters the commissioned task volume"


def test_furnished_capture_registers_only_supported_mug_and_tray(tmp_path, monkeypatch):
    from tangying_sim.rgbd_runtime import RgbdRuntimeService, RgbdTabletopWorld

    # Build a complete small pack instead of depending on downloaded assets or
    # mocking capture_scene. This exercises pack selection, world setup,
    # rendered RGB-D, perception and the actual public registration call.
    dae = _write_synthetic_dae(tmp_path / "source")
    pack = tmp_path / "pack"
    converted = convert_dae(dae, pack / "task-shapes")
    furnishing = convert_dae(dae, pack / "living_sofa")
    texture = pack / "wood.png"
    Image.new("RGB", (2,2), (115,91,62)).save(texture)
    manifest = {
        "schemaVersion": 1, "furnishings": [{"body":"living_sofa","targetSizeM":[1.1,2.2,.7],**furnishing}],
        "surfaces": {"home_floor_material": {"file":"wood.png",
            "sha256":hashlib.sha256(texture.read_bytes()).hexdigest()}},
        "taskAssets": {name:{"directory":"task-shapes","observeOnly":True,**converted}
                       for name in ("tableware","vase")},
    }
    (pack / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setenv("TANGYING_HOME_ASSET_PACK",str(pack))
    monkeypatch.delenv("TANGYING_NAVIGATION_URL",raising=False)
    runtime = RgbdRuntimeService(RgbdTabletopWorld.seeded(7,scene="home_task"))
    try:
        assert runtime.world.household
        assert set(runtime.world._pickable_joints) == {"ceramic-mug"}
        active = {"mapId":"test-measured-map","mapRevision":"scan-205",
                  "calibrationRevision":runtime.calibration.revision}
        runtime.workflow.active = active
        scene,pixels,public = runtime.capture_scene()
        assert pixels.rgb.size and pixels.depth_m.size and scene.points
        assert public["active_map"] == active
        assert public["semantic_navigation"]["mapRevision"] == active["mapRevision"]
        assert public["semantic_objects"] == [
            {"id":"ceramic-mug","category":"cup","attributes":{},"confidence":1.,"workArea":"kitchen"},
            {"id":"kitchen-tray","category":"storage_bin","attributes":{},"confidence":1.,"workArea":"kitchen"},
        ]
        assert all(not any(key in item for key in ("pose","position","pose_xyz_quat"))
                   for item in public["semantic_objects"])
        # An invalid active-map calibration gates the injected action catalog
        # just as it gates semantic room goals; no legacy fallback may escape.
        runtime.workflow.active = {**active,"calibrationRevision":"different-calibration"}
        _,_,invalid = runtime.capture_scene()
        assert "semantic_objects" not in invalid
        assert "semantic_navigation" not in invalid
    finally:
        runtime.close()


def test_the_survey_route_looks_back_at_where_it_started():
    """The floor under the start pose is the one patch a standstill look-around
    can never measure, and the finished map then refuses the first task
    dispatched from it with LOCALIZATION_NOT_CLEAR (measured: 13 unknown cells
    within 0.32 m). The route comes back and looks at it - without turning,
    because the driver allows only 0.5 rad of heading change per command.

    Measured effect of this one retreat: 13 -> 6 unknown cells in that disc. What
    is left is the base's own footprint, which no single viewpoint clears; a task
    that finds itself standing on uncertified ground is handled by the
    pre-position step instead of by relaxing the clearance check.
    """
    from tangying_sim.home_scene import HOME_WAYPOINTS
    from tangying_sim.workflow_services import WorkflowBindings

    class Service:
        class world:
            scene = "home_task"

    start = HOME_WAYPOINTS["living_room"]
    goals = WorkflowBindings.__new__(WorkflowBindings)
    goals.service = Service()
    route = WorkflowBindings.survey_goals(goals)

    look_backs = [goal for goal in route
                  if abs(goal[0] - start[0]) < 1e-9 and goal[1] < start[1] - 1e-6]
    assert look_backs, "the survey never returns to look at its own start"
    back = look_backs[-1]
    assert len(look_backs) == 1, "one retreat is all the living room has room for"
    assert start[1] - back[1] == pytest.approx(0.6)
    # Heading unchanged is what keeps it inside the driver's rotation budget.
    assert (back[3], back[6]) == (start[3], start[6])
    # And the retreat still leaves the footprint clear of the south wall.
    assert back[1] > -2.5 + 0.35, "the retreat must stay clear of the south wall"
