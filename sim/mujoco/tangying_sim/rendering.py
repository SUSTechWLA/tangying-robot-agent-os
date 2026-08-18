from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

import mujoco


@dataclass(frozen=True)
class Frame:
    data: bytes
    media_type: str


class SceneRenderer:
    def __init__(self, *, width: int = 320, height: int = 240):
        self.width = width
        self.height = height
        self.anomaly: str | None = None
        self._renderer: mujoco.Renderer | None = None
        self._model: mujoco.MjModel | None = None

    def render(self, model: mujoco.MjModel, data: mujoco.MjData) -> Frame | None:
        try:
            if self._renderer is None or self._model is not model:
                if self._renderer is not None:
                    self._renderer.close()
                self._renderer = mujoco.Renderer(model, height=self.height, width=self.width)
                self._model = model
            self._renderer.update_scene(data, camera="overview")
            rgb = self._renderer.render()
            self.anomaly = None
            return Frame(_encode_png(self.width, self.height, rgb.tobytes()), "image/png")
        except Exception as exc:  # noqa: BLE001 - rendering is explicitly best effort.
            self.anomaly = str(exc)
            return None


def _encode_png(width: int, height: int, rgb: bytes) -> bytes:
    stride = width * 3
    if len(rgb) != stride * height:
        raise ValueError("RGB byte count does not match frame dimensions")
    scanlines = b"".join(b"\x00" + rgb[offset : offset + stride] for offset in range(0, len(rgb), stride))
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return signature + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(scanlines)) + _chunk(b"IEND", b"")


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
