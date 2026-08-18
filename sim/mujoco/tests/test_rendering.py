import struct
import zlib

from tangying_sim.rendering import SceneRenderer
from tangying_sim.world import TabletopWorld


def test_renderer_returns_decodable_rgb_png():
    world = TabletopWorld.seeded(7)
    frame = SceneRenderer(width=96, height=72).render(world.model, world.data)

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


def test_renderer_discards_failed_backend_and_close_is_idempotent():
    world = TabletopWorld.seeded(7)
    renderer = SceneRenderer(width=16, height=12)

    class BrokenRenderer:
        def __init__(self):
            self.close_count = 0

        def update_scene(self, *_args, **_kwargs):
            raise RuntimeError("render backend lost")

        def close(self):
            self.close_count += 1

    broken = BrokenRenderer()
    renderer._renderer = broken
    renderer._model = world.model

    assert renderer.render(world.model, world.data) is None
    assert renderer.anomaly == "render backend lost"
    assert renderer._renderer is None
    assert broken.close_count == 1

    renderer.close()
    renderer.close()
    assert broken.close_count == 1


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
