from __future__ import annotations

import dataclasses
import struct
import zlib

import numpy as np
import pytest
from tangying_robot_gateway.rgbd import RgbdPerception
from tangying_robot_gateway.runtime import ObservationRequest

from .test_ros_rgbd import packets, profile, source, submit


def decode_rgb_png(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, bits, colour = struct.unpack(">IIBB", data[16:26])
    assert (bits, colour) == (8, 2)
    offset, compressed = 8, b""
    while offset < len(data):
        size = struct.unpack(">I", data[offset:offset+4])[0]
        kind = data[offset+4:offset+8]
        if kind == b"IDAT":
            compressed += data[offset+8:offset+8+size]
        offset += 12 + size
    rows = np.frombuffer(zlib.decompress(compressed), np.uint8).reshape(height, width*3+1)
    assert np.all(rows[:, 0] == 0)
    return rows[:, 1:].reshape(height, width, 3)


def test_reconstruction_and_rgb_depth_png_use_the_same_capture_when_next_frame_arrives():
    from tangying_robot_gateway.ros_rgbd import create_rgbd_backend

    input_source = source()
    first = packets()
    submit(input_source, first)

    def detector(frame):
        # A new camera frame arrives during processing. The observation image
        # must still show the first capture used by the current reconstruction.
        color, depth, info = packets(first[0].stamp_ns + 1_000_000)
        color = dataclasses.replace(color, data=bytes(len(color.data)))
        submit(input_source, (color, depth, info))
        return []

    backend = create_rgbd_backend(profile(), input_source, RgbdPerception(detector),
                                  stop=lambda reason: None)
    observation = backend.observe(ObservationRequest())
    assert observation.wall_time_unix_ms == first[0].stamp_ns // 1_000_000
    assert observation.image_media_type == observation.depth_image_media_type == "image/png"
    rgb = decode_rgb_png(observation.compressed_image)
    depth = decode_rgb_png(observation.compressed_depth_image)
    np.testing.assert_array_equal(rgb[0], [[255, 0, 0], [0, 255, 0]])
    np.testing.assert_array_equal(depth[1, 0], [0, 0, 0])  # zero depth is visibly invalid
    assert not np.array_equal(depth[0, 0], depth[0, 1])
    assert observation.reconstruction["points"][0] == [1., 2., 4.]


def test_capture_images_are_optional_for_legacy_plugin_and_invalid_media_is_rejected():
    from tangying_robot_gateway.plugin_backend import PluginBackend
    from tangying_robot_gateway.runtime import ReconstructionCapture

    input_source = source()
    submit(input_source, packets())
    reconstruction = RgbdPerception(lambda frame: []).reconstruct(input_source.read()).to_wire()
    legacy = PluginBackend(profile(), observation_provider=lambda: reconstruction, handlers={},
                            stop=lambda reason: None)
    assert legacy.observe(ObservationRequest()).compressed_image == b""
    capture = ReconstructionCapture(reconstruction, compressed_image=b"invalid", image_media_type="text/html")
    backend = PluginBackend(profile(), observation_provider=lambda: capture, handlers={},
                             stop=lambda reason: None)
    with pytest.raises(ValueError, match="image"):
        backend.observe(ObservationRequest())


def test_domain_images_survive_public_protobuf_conversion():
    from tangying_robot_gateway.ros_rgbd import create_rgbd_backend
    from tangying_robot_gateway.service import observation_to_proto

    input_source = source()
    submit(input_source, packets())
    backend = create_rgbd_backend(profile(), input_source, RgbdPerception(lambda frame: []),
                                  stop=lambda reason: None)
    observation = backend.observe(ObservationRequest())
    wire = observation_to_proto(observation)
    assert wire.compressed_image == observation.compressed_image
    assert wire.compressed_depth_image == observation.compressed_depth_image
    assert wire.depth_image_media_type == "image/png"


def test_provider_image_fields_are_read_once_and_published_as_validated_bytes():
    from tangying_robot_gateway.plugin_backend import PluginBackend
    from tangying_robot_gateway.rgbd_images import encode_rgb_png
    from tangying_robot_gateway.runtime import ReconstructionCapture

    input_source = source()
    submit(input_source, packets())
    frame = input_source.read()
    reconstruction = RgbdPerception(lambda frame: []).reconstruct(frame).to_wire()
    encoded = encode_rgb_png(frame.rgb)

    class ChangingCapture(ReconstructionCapture):
        def __getattribute__(self, name):
            if name == "compressed_image":
                reads = object.__getattribute__(self, "reads")
                object.__setattr__(self, "reads", reads + 1)
                return encoded if reads == 0 else b"changed after validation"
            return super().__getattribute__(name)

    capture = ChangingCapture(reconstruction, image_media_type="image/png")
    object.__setattr__(capture, "reads", 0)
    backend = PluginBackend(profile(), observation_provider=lambda: capture, handlers={},
                             stop=lambda reason: None)
    assert backend.observe(ObservationRequest()).compressed_image == encoded
