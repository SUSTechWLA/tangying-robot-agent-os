"""The log that makes every later comparison possible.

A sensor log is only worth having if a replay is the same replay twice. These
tests are about that property, and about refusing a log that cannot be replayed
rather than producing frames that are quietly wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from tangying_robot_gateway.rgbd import RgbdFrame
from tangying_robot_gateway.sensor_log import (
    SCHEMA,
    SensorLogError,
    SensorLogReader,
    SensorLogWriter,
)

CALIBRATION = "c" * 64


def a_frame(sequence: int) -> RgbdFrame:
    height, width = 6, 8
    rng = np.random.default_rng(sequence)
    return RgbdFrame(
        robot_id="unit-1", source_id="unit-1/base-rgbd", frame_id="base_depth_optical",
        transform_revision=CALIBRATION, captured_at_unix_ms=1_700_000_000_000 + sequence * 33,
        sequence=sequence,
        rgb=rng.integers(0, 256, (height, width, 3), dtype=np.uint8),
        depth_m=rng.uniform(0.3, 4.0, (height, width)),
        intrinsics=np.array([[80.0, 0, 3.5], [0, 80.0, 2.5], [0, 0, 1.0]]),
        world_from_camera=np.eye(4),
    )


def write_log(directory: Path, count: int = 4) -> dict:
    writer = SensorLogWriter(directory, robot_id="unit-1",
                             calibration_revision=CALIBRATION, source="unit-test",
                             metadata={"scene": "furnished-home"})
    for sequence in range(1, count + 1):
        writer.append(a_frame(sequence), joints={"head_tilt": 0.1 * sequence},
                      base_pose=(0.1 * sequence, 0.0, 0.035, 1.0, 0.0, 0.0, 0.0))
    return writer.close()


def test_a_recorded_frame_comes_back_byte_for_byte(tmp_path: Path):
    write_log(tmp_path)
    frames = list(SensorLogReader(tmp_path))
    assert len(frames) == 4
    for index, logged in enumerate(frames, start=1):
        original = a_frame(index)
        np.testing.assert_array_equal(logged.frame.rgb, original.rgb)
        # Depth is stored as float32 and the frame carries float64; the arrays must
        # be equal at the precision the sensor actually has, not bit-identical.
        np.testing.assert_array_equal(
            logged.frame.depth_m, original.depth_m.astype("<f4").astype(np.float64))
        assert logged.frame.captured_at_unix_ms == original.captured_at_unix_ms
        assert logged.frame.sequence == original.sequence
        np.testing.assert_array_equal(logged.frame.intrinsics, original.intrinsics)
        assert logged.joints == {"head_tilt": pytest.approx(0.1 * index)}
        assert logged.base_pose is not None and logged.base_pose[0] == pytest.approx(0.1 * index)


def test_two_replays_of_one_log_agree_on_every_digest(tmp_path: Path):
    """The property the whole exercise rests on: a comparison is only meaningful
    if both runs were fed the same frames."""
    write_log(tmp_path)
    first = [logged.digest for logged in SensorLogReader(tmp_path)]
    second = [logged.digest for logged in SensorLogReader(tmp_path)]
    assert first == second
    assert SensorLogReader(tmp_path).verify()["logDigest"] == write_log(tmp_path)["logDigest"]


def test_a_frame_altered_in_the_log_is_refused_rather_than_replayed(tmp_path: Path):
    write_log(tmp_path)
    blob = tmp_path / "rgb.bin"
    raw = bytearray(blob.read_bytes())
    raw[3] ^= 0xFF
    blob.write_bytes(bytes(raw))
    with pytest.raises(SensorLogError) as failure:
        list(SensorLogReader(tmp_path))
    assert failure.value.code == "LOG_CORRUPT"


def test_a_frame_removed_from_the_log_changes_its_identity(tmp_path: Path):
    write_log(tmp_path)
    before = SensorLogReader(tmp_path).verify()["logDigest"]
    index = tmp_path / "index.jsonl"
    lines = index.read_text(encoding="utf-8").splitlines(True)
    index.write_text("".join(lines[1:]), encoding="utf-8")
    with pytest.raises(SensorLogError) as failure:
        SensorLogReader(tmp_path).verify()
    assert failure.value.code == "LOG_FRAME_COUNT"
    assert SensorLogReader(tmp_path).manifest["logDigest"] == before


def test_a_truncated_blob_is_named_not_replayed_short(tmp_path: Path):
    write_log(tmp_path)
    blob = tmp_path / "depth.bin"
    blob.write_bytes(blob.read_bytes()[:-16])
    with pytest.raises(SensorLogError) as failure:
        list(SensorLogReader(tmp_path))
    assert failure.value.code == "LOG_TRUNCATED"


def test_a_log_for_another_robot_cannot_be_appended_to(tmp_path: Path):
    writer = SensorLogWriter(tmp_path, robot_id="unit-2",
                             calibration_revision=CALIBRATION, source="unit-test")
    with pytest.raises(SensorLogError) as failure:
        writer.append(a_frame(1))
    assert failure.value.code == "LOG_ROBOT_MISMATCH"
    writer.close()


def test_a_log_without_an_identity_is_refused(tmp_path: Path):
    with pytest.raises(SensorLogError) as failure:
        SensorLogWriter(tmp_path / "x", robot_id="", calibration_revision=CALIBRATION,
                        source="unit-test")
    assert failure.value.code == "LOG_IDENTITY_REQUIRED"


def test_the_manifest_says_what_was_recorded_and_how(tmp_path: Path):
    manifest = write_log(tmp_path, count=3)
    assert manifest["schemaVersion"] == SCHEMA
    assert manifest["frames"] == 3
    assert manifest["robotId"] == "unit-1"
    assert manifest["calibrationRevision"] == CALIBRATION
    assert manifest["metadata"]["scene"] == "furnished-home"
    # The encoding is stated rather than implied: a reader has to be able to tell
    # that depth is float32 metres and not, say, uint16 millimetres.
    assert manifest["encoding"]["depth"] == "float32-le-metres-row-major"
    assert manifest["bytes"]["rgb"] == 3 * 6 * 8 * 3
    assert len(manifest["logDigest"]) == 64
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8")) == manifest


def test_finishing_twice_is_harmless(tmp_path: Path):
    writer = SensorLogWriter(tmp_path, robot_id="unit-1",
                             calibration_revision=CALIBRATION, source="unit-test")
    writer.append(a_frame(1))
    first = writer.close()
    assert writer.close() == first


def test_a_log_from_another_schema_is_refused_by_name(tmp_path: Path):
    write_log(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    manifest["schemaVersion"] = "robot.sensor_log.v99"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SensorLogError) as failure:
        SensorLogReader(tmp_path)
    assert failure.value.code == "LOG_SCHEMA"


def test_reading_a_directory_that_is_not_a_log_says_so(tmp_path: Path):
    with pytest.raises(SensorLogError) as failure:
        SensorLogReader(tmp_path)
    assert failure.value.code == "LOG_MISSING"
