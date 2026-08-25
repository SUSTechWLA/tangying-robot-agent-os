import struct
import threading
import zlib

import numpy as np
from tangying_sim import rendering
from tangying_sim.rendering import SceneRenderer
from tangying_sim.world import TabletopWorld


def test_renderer_returns_decodable_rgb_png():
    world = TabletopWorld.seeded(7)
    renderer = SceneRenderer(width=96, height=72)
    frame = renderer.render(world.model, world.data)
    renderer.close()

    assert frame is not None
    assert frame.media_type == "image/png"
    assert frame.data.startswith(b"\x89PNG\r\n\x1a\n")

    chunks = _chunks(frame.data)
    assert [kind for kind, _payload in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    width, height, depth, color_type, _compression, _filter, _interlace = struct.unpack(
        ">IIBBBBB", chunks[0][1]
    )
    assert (width, height, depth, color_type) == (96, 72, 8, 2)
    assert len(zlib.decompress(chunks[1][1])) == height * (1 + width * 3)


def test_renderer_returns_rgb_and_metric_depth_from_one_camera():
    world = TabletopWorld.seeded(7)
    renderer = SceneRenderer(width=96, height=72)
    capture = renderer.render_capture(world.model, world.data, camera="overview")
    renderer.close()

    assert capture is not None
    assert capture.rgb.media_type == "image/png"
    assert capture.depth.media_type == "image/png;depth=uint16-mm"
    assert _ihdr(capture.rgb.data)[:4] == (96, 72, 8, 2)
    assert _ihdr(capture.depth.data)[:4] == (96, 72, 16, 0)
    assert len(capture.rgb.sha256) == 64
    assert len(capture.depth.sha256) == 64
    assert len(capture.intrinsics) == 9
    assert len(capture.camera_to_world) == 16
    assert capture.depth_scale_m == 0.001
    assert 0 < capture.min_range_m < capture.max_range_m


def test_renderer_discards_failed_backend_and_close_is_idempotent(monkeypatch):
    world = TabletopWorld.seeded(7)

    class BrokenRenderer:
        def __init__(self):
            self.close_count = 0

        def update_scene(self, *_args, **_kwargs):
            raise RuntimeError("render backend lost")

        def close(self):
            self.close_count += 1

    broken = BrokenRenderer()
    monkeypatch.setattr(
        rendering.mujoco,
        "Renderer",
        lambda *_args, **_kwargs: broken,
    )
    renderer = SceneRenderer(width=16, height=12)

    assert renderer.render(world.model, world.data) is None
    assert renderer.anomaly == "render backend lost"
    assert broken.close_count == 1
    renderer.close()
    renderer.close()
    assert broken.close_count == 1


def test_renderer_gl_lifecycle_runs_on_dedicated_owner_thread(monkeypatch):
    world = TabletopWorld.seeded(7)
    calls = []

    class ThreadRecordingRenderer:
        def __init__(self, *_args, **_kwargs):
            calls.append(("create", threading.get_ident()))

        def update_scene(self, *_args, **_kwargs):
            calls.append(("update", threading.get_ident()))

        def render(self):
            calls.append(("render", threading.get_ident()))
            return np.zeros((12, 16, 3), dtype=np.uint8)

        def close(self):
            calls.append(("close", threading.get_ident()))

    monkeypatch.setattr(rendering.mujoco, "Renderer", ThreadRecordingRenderer)
    caller_thread = threading.get_ident()
    renderer = SceneRenderer(width=16, height=12)

    assert renderer.render(world.model, world.data) is not None
    renderer.close()

    owner_threads = {thread_id for _operation, thread_id in calls}
    assert len(owner_threads) == 1
    assert owner_threads != {caller_thread}

def _chunks(data):
    chunks = []
    offset = 8
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        chunks.append((kind, payload))
        offset += 12 + length
    return chunks


def _ihdr(data):
    chunks = _chunks(data)
    assert chunks[0][0] == b"IHDR"
    return struct.unpack(">IIBBBBB", chunks[0][1])
