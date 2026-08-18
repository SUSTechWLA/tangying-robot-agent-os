from __future__ import annotations

import struct
import zlib
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass
from queue import Queue
from threading import Lock, Thread

import mujoco


@dataclass(frozen=True)
class Frame:
    data: bytes
    media_type: str


@dataclass(frozen=True)
class _RenderRequest:
    operation: str
    future: Future[Frame | None]
    model: mujoco.MjModel | None = None
    data: mujoco.MjData | None = None


class SceneRenderer:
    def __init__(self, *, width: int = 320, height: int = 240):
        self.width = width
        self.height = height
        self.anomaly: str | None = None
        self._renderer: mujoco.Renderer | None = None
        self._model: mujoco.MjModel | None = None
        self._state_lock = Lock()
        self._closed = False
        self._requests: Queue[_RenderRequest] = Queue()
        self._owner = Thread(
            target=self._run,
            name="mujoco-renderer-owner",
            daemon=True,
        )
        self._owner.start()

    def render(self, model: mujoco.MjModel, data: mujoco.MjData) -> Frame | None:
        future: Future[Frame | None] = Future()
        with self._state_lock:
            if self._closed:
                self.anomaly = "renderer is closed"
                return None
            self._requests.put(_RenderRequest("render", future, model, data))
        return future.result()

    def close(self) -> None:
        future: Future[Frame | None] = Future()
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._requests.put(_RenderRequest("close", future))
        future.result()
        self._owner.join()

    def _run(self) -> None:
        while True:
            request = self._requests.get()
            if request.operation == "close":
                self._discard_renderer()
                request.future.set_result(None)
                return
            try:
                frame = self._render_owned(request.model, request.data)
            except Exception as exc:  # noqa: BLE001 - rendering is explicitly best effort.
                self.anomaly = str(exc)
                self._discard_renderer()
                frame = None
            request.future.set_result(frame)

    def _render_owned(
        self, model: mujoco.MjModel | None, data: mujoco.MjData | None
    ) -> Frame:
        if model is None or data is None:
            raise ValueError("render request requires model and data")
        if self._renderer is None or self._model is not model:
            self._discard_renderer()
            self._renderer = mujoco.Renderer(
                model, height=self.height, width=self.width
            )
            self._model = model
        self._renderer.update_scene(data, camera="overview")
        rgb = self._renderer.render()
        self.anomaly = None
        return Frame(_encode_png(self.width, self.height, rgb.tobytes()), "image/png")

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
