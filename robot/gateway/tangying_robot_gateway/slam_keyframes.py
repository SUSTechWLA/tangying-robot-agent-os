"""Bounded, capture-time previews for immutable SLAM keyframe inspection.

These are explicitly downsampled previews of accepted sensor captures, not a
sensor archive or rendered replays. Missing evidence is never reconstructed.
"""
from __future__ import annotations

import base64
import hashlib
import io

import numpy as np
from PIL import Image

from .rgbd_images import encode_depth_preview

MAX_KEYFRAMES = 400
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PAIR_BYTES = 128 * 1024
MAX_ARTIFACT_BYTES = 12 * 1024 * 1024
PREVIEW_SIZE = (240, 180)


def capture_metadata(observation, point_count):
    sensor = observation.rgbd_frame
    source = dict(observation.reconstruction)
    depth = np.frombuffer(sensor.depth_metres_f32, dtype="<f4")
    valid = np.isfinite(depth) & (depth > .15) & (depth < 5.)
    masked = (np.frombuffer(sensor.robot_self_mask, dtype=np.uint8) != 0
              if sensor.robot_self_mask else np.zeros(depth.shape, dtype=bool))
    measured = depth[valid]
    return {
        "sourceId": str(source.get("sourceId", ""))[:256],
        "cameraFrameId": str(source.get("sourceFrameId", ""))[:256],
        "transformRevision": str(source.get("transformRevision", ""))[:256],
        "width": sensor.width, "height": sensor.height,
        "intrinsics": list(sensor.intrinsics), "baseFromCamera": list(sensor.base_from_camera),
        "selfFilterModelRevision": sensor.self_filter_model_revision[:256],
        "pointCount": point_count, "validDepthPixels": int(valid.sum()),
        "integratedDepthPixels": int((valid & ~masked).sum()),
        "selfMaskedPixels": int(masked.sum()),
        "depthMinM": float(measured.min()) if measured.size else None,
        "depthMaxM": float(measured.max()) if measured.size else None,
    }


def _image(payload, media_type, size):
    return {"mediaType": media_type, "width": size[0], "height": size[1],
            "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
            "data": base64.b64encode(payload).decode("ascii")}


class KeyframePreviews:
    def __init__(self, max_bytes=MAX_IMAGE_BYTES):
        self.max_bytes = max(0, min(int(max_bytes), MAX_IMAGE_BYTES))
        self.bytes = 0
        self.frames = []

    def add(self, observation, frame_id):
        if len(self.frames) >= MAX_KEYFRAMES:
            raise ValueError("keyframe image count exceeds session budget")
        entry = {"frameId": frame_id, "observationId": observation.observation_id,
                 "stamp": observation.wall_time_unix_ms, "status": "budget_exhausted"}
        self.frames.append(entry)
        if self.bytes >= self.max_bytes:
            return entry["status"]
        sensor = observation.rgbd_frame
        rgb = Image.fromarray(np.frombuffer(sensor.rgb, dtype=np.uint8).reshape(sensor.height, sensor.width, 3))
        rgb.thumbnail(PREVIEW_SIZE, Image.Resampling.BILINEAR)
        size = rgb.size
        stream = io.BytesIO()
        rgb.save(stream, format="JPEG", quality=78, optimize=False)
        rgb_bytes = stream.getvalue()
        depth = np.frombuffer(sensor.depth_metres_f32, dtype="<f4").reshape(sensor.height, sensor.width)
        # Nearest sample avoids inventing intermediate depth at object edges.
        small = np.asarray(Image.fromarray(depth).resize(size, Image.Resampling.NEAREST))
        depth_bytes = encode_depth_preview(small)
        count = len(rgb_bytes) + len(depth_bytes)
        if count > MAX_PAIR_BYTES or self.bytes + count > self.max_bytes:
            return entry["status"]
        self.bytes += count
        entry.update(status="saved", rgb=_image(rgb_bytes, "image/jpeg", size),
                     depth=_image(depth_bytes, "image/png", size))
        return entry["status"]

    def document(self, *, map_id, robot_id, calibration_revision):
        return {"schemaVersion": "slam.keyframes.v1", "mapId": map_id, "frameId": "map",
                "robotId": robot_id, "calibrationRevision": calibration_revision,
                "encoding": {"kind": "capture_previews", "maxWidth": PREVIEW_SIZE[0],
                             "maxHeight": PREVIEW_SIZE[1], "rgb": "jpeg-quality-78",
                             "depth": "nearest-sample-fixed-scale-preview",
                             "depthRangeM": [.02, 5.], "invalidDepth": "black",
                             "depthColors": "near-warm-far-cool", "rawDepthSaved": False},
                "budget": {"maxFrames": MAX_KEYFRAMES, "maxImageBytes": self.max_bytes,
                           "imageBytes": self.bytes, "maxArtifactBytes": MAX_ARTIFACT_BYTES},
                "frames": self.frames}
