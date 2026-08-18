from __future__ import annotations

import struct
import zlib
from contextlib import suppress
from dataclasses import dataclass
from threading import RLock

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
        self._lock = RLock()

    def render(self, model: mujoco.MjModel, data: mujoco.MjData) -> Frame | None:
        with self._lock:
            try:
                if self._renderer is None or self._model is not model:
                    self._discard_renderer()
                    self._renderer = mujoco.Renderer(
                        model, height=self.height, width=self.width
                    )
                    self._model = model
                self._renderer.update_scene(data, camera="overview")
                rgb = self._renderer.render()
                self.anomaly = None
                return Frame(
                    _encode_png(self.width, self.height, rgb.tobytes()), "image/png"
                )
            except Exception as exc:  # noqa: BLE001 - rendering is explicitly best effort.
                self.anomaly = str(exc)
                self._discard_renderer()
                return None

    def close(self) -> None:
        with self._lock:
            self._discard_renderer()

    def _discard_renderer(self) -> None:
        renderer = self._renderer
        self._renderer = None
        self._model = None
        if renderer is not None:
            with suppress(Exception):
                renderer.close()


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
