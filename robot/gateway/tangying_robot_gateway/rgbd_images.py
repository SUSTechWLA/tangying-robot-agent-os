"""PNG transport for the same RGB-D capture used by metric reconstruction.

Depth preview is a fixed-scale display image, not a replacement for depth_m:
warm colours are near, cool colours far, invalid/out-of-range pixels are black.
"""
from __future__ import annotations

import math
import struct
import zlib

import numpy as np

MAX_IMAGE_BYTES = 2 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def encode_rgb_png(rgb: np.ndarray) -> bytes:
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("image must be RGB uint8 HxWx3")
    height, width, _ = rgb.shape
    if not 0 < width <= 3840 or not 0 < height <= 2160:
        raise ValueError("image dimensions exceed bounded RGB-D capture size")
    rows = b"".join(b"\0" + row.tobytes() for row in rgb)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    result = PNG_SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b"")
    if len(result) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds bounded PNG transport size; lower camera resolution")
    return result


def encode_depth_preview(depth_m: np.ndarray, max_depth_m: float = 5.0) -> bytes:
    if (not isinstance(depth_m, np.ndarray) or depth_m.ndim != 2 or depth_m.dtype.kind != "f"
            or not 0 < depth_m.shape[0] <= 2160 or not 0 < depth_m.shape[1] <= 3840):
        raise ValueError("depth image must be bounded floating-point metres HxW")
    if type(max_depth_m) not in (int, float) or not math.isfinite(max_depth_m) or max_depth_m <= .02:
        raise ValueError("depth display maximum must exceed 0.02 metres")
    valid = np.isfinite(depth_m) & (depth_m > .02) & (depth_m <= max_depth_m)
    scale = np.clip((np.where(valid, depth_m, .02) - .02) / (max_depth_m - .02), 0., 1.)
    preview = np.stack((1.-scale, 1.-np.abs(2.*scale-1.), scale), axis=-1)
    rgb = np.rint(preview * 255.).astype(np.uint8)
    rgb[~valid] = 0
    return encode_rgb_png(rgb)


def validate_observation_images(value) -> dict:
    """Validate and snapshot image fields once at the common public boundary."""
    fields = {name: getattr(value, name) for name in (
        "compressed_image", "image_media_type", "compressed_depth_image", "depth_image_media_type",
    )}
    total = 0
    dimensions = []
    for data, media_type in (
        (fields["compressed_image"], fields["image_media_type"]),
        (fields["compressed_depth_image"], fields["depth_image_media_type"]),
    ):
        if type(data) is not bytes or type(media_type) is not str:
            raise ValueError("observation image fields must be immutable bytes and media-type text")
        if not data:
            if media_type:
                raise ValueError("empty observation image cannot declare a media type")
            continue
        if (media_type != "image/png" or len(data) < 45 or len(data) > MAX_IMAGE_BYTES
                or not data.startswith(PNG_SIGNATURE) or data[8:16] != b"\0\0\0\rIHDR"):
            raise ValueError("observation image must be a bounded PNG")
        width, height, bits, colour = struct.unpack(">IIBB", data[16:26])
        if not 0 < width <= 3840 or not 0 < height <= 2160 or (bits, colour) != (8, 2):
            raise ValueError("observation image must use bounded RGB8 PNG dimensions")
        dimensions.append((width, height))
        total += len(data)
    if total > MAX_IMAGE_BYTES:
        raise ValueError("RGB plus depth image payload exceeds the 2 MiB transport budget")
    if len(dimensions) == 2 and dimensions[0] != dimensions[1]:
        raise ValueError("RGB and depth observation images must have matching dimensions")
    return fields
