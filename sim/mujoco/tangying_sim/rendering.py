from __future__ import annotations

import hashlib
import math
import struct
import zlib
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass
from queue import Queue
from threading import Lock, Thread

import mujoco
import numpy as np


@dataclass(frozen=True)
class RenderedFrame:
    data: bytes
    media_type: str
    sha256: str


@dataclass(frozen=True)
class RenderedCapture:
    rgb: RenderedFrame
    depth: RenderedFrame
    width: int
    height: int
    intrinsics: tuple[float, ...]
    camera_to_world: tuple[float, ...]
    depth_scale_m: float
    min_range_m: float
    max_range_m: float


# Compatibility name for callers that only consume the operator RGB frame.
Frame = RenderedFrame


@dataclass(frozen=True)
class _RenderRequest:
    operation: str
    future: Future[RenderedFrame | RenderedCapture | None]
    model: mujoco.MjModel | None = None
    data: mujoco.MjData | None = None
    camera: str = "overview"


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

    def render(self, model: mujoco.MjModel, data: mujoco.MjData) -> RenderedFrame | None:
        future: Future[RenderedFrame | RenderedCapture | None] = Future()
        with self._state_lock:
            if self._closed:
                self.anomaly = "renderer is closed"
                return None
            self._requests.put(_RenderRequest("rgb", future, model, data, "overview"))
        result = future.result()
        return result if isinstance(result, RenderedFrame) else None

    def render_capture(
        self,
        model: mujoco.MjModel,
        data: mujoco.MjData,
        *,
        camera: str,
    ) -> RenderedCapture | None:
        future: Future[RenderedFrame | RenderedCapture | None] = Future()
        with self._state_lock:
            if self._closed:
                self.anomaly = "renderer is closed"
                return None
            self._requests.put(_RenderRequest("rgbd", future, model, data, camera))
        result = future.result()
        return result if isinstance(result, RenderedCapture) else None

    def close(self) -> None:
        future: Future[RenderedFrame | RenderedCapture | None] = Future()
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
                if request.operation == "rgbd":
                    result = self._render_capture_owned(
                        request.model, request.data, request.camera
                    )
                else:
                    result = self._render_rgb_owned(
                        request.model, request.data, request.camera
                    )
            except Exception as exc:  # noqa: BLE001 - rendering is explicitly best effort.
                self.anomaly = str(exc)
                self._discard_renderer()
                result = None
            request.future.set_result(result)

    def _ensure_renderer(
        self, model: mujoco.MjModel | None, data: mujoco.MjData | None
    ) -> tuple[mujoco.MjModel, mujoco.MjData, mujoco.Renderer]:
        if model is None or data is None:
            raise ValueError("render request requires model and data")
        if self._renderer is None or self._model is not model:
            self._discard_renderer()
            self._renderer = mujoco.Renderer(
                model, height=self.height, width=self.width
            )
            self._model = model
        return model, data, self._renderer

    def _render_rgb_owned(
        self, model: mujoco.MjModel | None, data: mujoco.MjData | None, camera: str
    ) -> RenderedFrame:
        _model, data, renderer = self._ensure_renderer(model, data)
        renderer.update_scene(data, camera=camera)
        rgb = renderer.render()
        encoded = _encode_png(self.width, self.height, rgb.tobytes())
        self.anomaly = None
        return RenderedFrame(encoded, "image/png", _sha256(encoded))

    def _render_capture_owned(
        self, model: mujoco.MjModel | None, data: mujoco.MjData | None, camera: str
    ) -> RenderedCapture:
        model, data, renderer = self._ensure_renderer(model, data)
        camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, camera)
        if camera_id < 0:
            raise ValueError(f"unknown MuJoCo camera: {camera}")

        renderer.update_scene(data, camera=camera)
        rgb_pixels = renderer.render()
        renderer.enable_depth_rendering()
        try:
            depth_metres = np.asarray(renderer.render(), dtype=np.float64)
        finally:
            renderer.disable_depth_rendering()

        rgb_data = _encode_png(self.width, self.height, rgb_pixels.tobytes())
        min_range_m, max_range_m = _depth_range(model)
        depth_data = _encode_metric_depth_png(
            self.width,
            self.height,
            depth_metres,
            min_range_m=min_range_m,
            max_range_m=max_range_m,
        )
        self.anomaly = None
        return RenderedCapture(
            rgb=RenderedFrame(rgb_data, "image/png", _sha256(rgb_data)),
            depth=RenderedFrame(
                depth_data,
                "image/png;depth=uint16-mm",
                _sha256(depth_data),
            ),
            width=self.width,
            height=self.height,
            intrinsics=_intrinsics(model, camera_id, self.width, self.height),
            camera_to_world=_camera_to_world(data, camera_id),
            depth_scale_m=0.001,
            min_range_m=min_range_m,
            max_range_m=max_range_m,
        )

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
    scanlines = b"".join(
        b"\x00" + rgb[offset : offset + stride]
        for offset in range(0, len(rgb), stride)
    )
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return signature + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(scanlines)) + _chunk(b"IEND", b"")


def _encode_metric_depth_png(
    width: int,
    height: int,
    depth_metres: np.ndarray,
    *,
    min_range_m: float,
    max_range_m: float,
) -> bytes:
    if depth_metres.shape != (height, width):
        raise ValueError("depth dimensions do not match frame dimensions")
    valid = (
        np.isfinite(depth_metres)
        & (depth_metres >= min_range_m)
        & (depth_metres <= max_range_m)
    )
    millimetres = np.zeros((height, width), dtype=np.uint16)
    millimetres[valid] = np.clip(
        np.rint(depth_metres[valid] * 1000.0), 1, np.iinfo(np.uint16).max
    ).astype(np.uint16)
    big_endian = millimetres.astype(">u2", copy=False).tobytes()
    stride = width * 2
    scanlines = b"".join(
        b"\x00" + big_endian[offset : offset + stride]
        for offset in range(0, len(big_endian), stride)
    )
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 16, 0, 0, 0, 0)
    return (
        signature
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(scanlines))
        + _chunk(b"IEND", b"")
    )


def _intrinsics(
    model: mujoco.MjModel, camera_id: int, width: int, height: int
) -> tuple[float, ...]:
    fovy = math.radians(float(model.cam_fovy[camera_id]))
    focal = (height / 2.0) / math.tan(fovy / 2.0)
    return (
        focal,
        0.0,
        (width - 1) / 2.0,
        0.0,
        focal,
        (height - 1) / 2.0,
        0.0,
        0.0,
        1.0,
    )


def _camera_to_world(data: mujoco.MjData, camera_id: int) -> tuple[float, ...]:
    rotation = np.asarray(data.cam_xmat[camera_id], dtype=np.float64).reshape(3, 3)
    position = np.asarray(data.cam_xpos[camera_id], dtype=np.float64)
    return (
        float(rotation[0, 0]), float(rotation[0, 1]), float(rotation[0, 2]), float(position[0]),
        float(rotation[1, 0]), float(rotation[1, 1]), float(rotation[1, 2]), float(position[1]),
        float(rotation[2, 0]), float(rotation[2, 1]), float(rotation[2, 2]), float(position[2]),
        0.0, 0.0, 0.0, 1.0,
    )


def _depth_range(model: mujoco.MjModel) -> tuple[float, float]:
    extent = max(float(model.stat.extent), 1e-6)
    near = max(float(model.vis.map.znear) * extent, 1e-6)
    far = min(float(model.vis.map.zfar) * extent, 65.535)
    if far <= near:
        far = min(max(near + 0.001, near * 2.0), 65.535)
    return near, far


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
