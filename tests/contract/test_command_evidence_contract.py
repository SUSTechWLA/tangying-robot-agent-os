"""A tool result must be able to carry the exact capture used for verification."""

from tangying_robot_proto.robot.v1 import robot_pb2


def test_skill_result_can_preserve_original_verification_capture():
    field = robot_pb2.SkillEvent.DESCRIPTOR.fields_by_name.get("evidence_observation")
    assert field is not None, "verification results currently only reference an unavailable capture ID"
    assert field.number == 11
    assert field.message_type.full_name == robot_pb2.Observation.DESCRIPTOR.full_name
    source = robot_pb2.Observation(
        observation_id="verification-camera-frame", compressed_image=b"original-rgb"
    )
    event = robot_pb2.SkillEvent(observation_id=source.observation_id)
    event.evidence_observation.CopyFrom(source)
    restored = robot_pb2.SkillEvent.FromString(event.SerializeToString())
    assert restored.evidence_observation == source


def test_slam_stream_carries_metric_depth_and_extrinsics_without_png_decoding():
    import struct

    field = robot_pb2.Observation.DESCRIPTOR.fields_by_name.get("rgbd_frame")
    assert field is not None, "SLAM requires metric depth, not the display depth PNG"
    assert field.number == 12
    raw = robot_pb2.RGBDFrame(
        width=2, height=1, rgb=b"\xff\x00\x00\x00\xff\x00",
        depth_metres_f32=struct.pack("<ff", 0.75, 1.25),
        intrinsics=[300, 0, 1, 0, 300, 0.5, 0, 0, 1],
        base_from_camera=[1, 0, 0, 0.2, 0, 1, 0, 0, 0, 0, 1, 0.1, 0, 0, 0, 1],
        robot_self_mask=bytes([1, 0]), self_filter_model_revision="robot-only-cad-v1",
    )
    obs = robot_pb2.Observation(observation_id="raw-capture", rgbd_frame=raw)
    restored = robot_pb2.Observation.FromString(obs.SerializeToString())
    assert struct.unpack("<ff", restored.rgbd_frame.depth_metres_f32) == (0.75, 1.25)
    assert restored.rgbd_frame.base_from_camera[3] == 0.2
    assert restored.rgbd_frame.robot_self_mask == bytes([1, 0])
    assert restored.rgbd_frame.self_filter_model_revision == "robot-only-cad-v1"
