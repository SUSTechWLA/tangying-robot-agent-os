import io
import time

import numpy as np
import pytest
from PIL import Image
from tangying_robot_proto.robot.v1 import robot_pb2
from tangying_sim.fleet_server import create_fleet_services
from tangying_sim.rgbd_navigation import load_navigation_model
from tangying_sim.shared_handoff import seeded_handoff_worlds


@pytest.fixture
def fleet_services():
    _, services = create_fleet_services(seed=7)
    try:
        yield services
    finally:
        for service in services.values():
            service.close()


def test_fleet_shadow_budget_preserves_lighting_camera_and_physics(fleet_services):
    _, sender, receiver = seeded_handoff_worlds(seed=7)
    for reference in (sender, receiver):
        service = fleet_services[reference.robot_id]
        model = service.world.model
        expected = reference.model
        key = model.light("task_key").id
        assert np.flatnonzero(model.light_castshadow).tolist() == [key]
        assert model.vis.quality.shadowsize == expected.vis.quality.shadowsize == 2048
        assert model.vis.quality.offsamples == expected.vis.quality.offsamples == 4
        assert service.renderer.width == 320 and service.renderer.height == 240
        assert service.renderer.camera == "overview"
        for field in (
            "light_active", "light_pos", "light_dir", "light_diffuse",
            "light_specular", "light_ambient", "cam_pos", "cam_quat", "cam_fovy",
            "geom_pos", "geom_quat", "geom_size", "geom_type", "geom_matid",
            "geom_contype", "geom_conaffinity", "mesh_vert", "mesh_face",
        ):
            np.testing.assert_array_equal(getattr(model, field), getattr(expected, field))


def test_fleet_factory_does_not_change_native_rgbd_shadow_configuration(fleet_services):
    native = load_navigation_model()
    assert native.nlight == 3
    assert native.light_castshadow.all()
    assert native.vis.quality.shadowsize == 2048
    assert native.vis.quality.offsamples == 4
    assert all(service.world.model is not native for service in fleet_services.values())


def test_both_fleet_cameras_return_original_png_and_pre_render_timestamp(fleet_services, monkeypatch):
    for service in fleet_services.values():
        render = service.renderer.render
        render_times = []

        def timed_render(model, data, original=render, times=render_times):
            times.append(time.time_ns() // 1_000_000)
            return original(model, data)

        monkeypatch.setattr(service.renderer, "render", timed_render)
        before = time.time_ns() // 1_000_000
        observation = next(service.Observe(robot_pb2.ObserveRequest(), None))
        assert before <= observation.wall_time_unix_ms <= render_times[0]
        assert observation.image_media_type == "image/png"
        assert len(observation.compressed_image) > 1000
        with Image.open(io.BytesIO(observation.compressed_image)) as image:
            assert image.size == (320, 240)
            assert image.mode == "RGB"
        assert observation.entities
        assert observation.robot_state
