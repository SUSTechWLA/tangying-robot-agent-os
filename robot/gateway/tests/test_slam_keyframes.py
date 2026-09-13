import base64
import hashlib
import io
import json

import numpy as np
import pytest
from PIL import Image
from tangying_robot_gateway.dense_slam import DenseSLAM
from tangying_robot_gateway.map_manifest import load_manifest, verify_artifacts
from tangying_robot_gateway.map_pipeline import build_map
from tangying_robot_gateway.slam_keyframes import KeyframePreviews
from tangying_robot_proto.robot.v1 import robot_pb2


def observation(x=0., stamp=1000, width=320, height=240):
    rows, cols = np.mgrid[:height, :width]
    depth = (2.+.1*np.sin(cols/7)+.15*np.cos(rows/9)).astype('<f4')
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[..., 0] = 200
    value = robot_pb2.Observation(observation_id=f'capture-{stamp}', wall_time_unix_ms=stamp)
    value.robot_state.update({'base_pose': [x, 0, .035, 1, 0, 0, 0], 'hidden_object_pose': [99, 99, 99]})
    value.reconstruction.update({'sourceId': 'unit/base-rgbd', 'sourceFrameId': 'base_optical', 'transformRevision': 'mount-v1'})
    value.rgbd_frame.CopyFrom(robot_pb2.RGBDFrame(width=width, height=height, rgb=rgb.tobytes(),
        depth_metres_f32=depth.tobytes(), intrinsics=[200, 0, width/2, 0, 200, height/2, 0, 0, 1],
        base_from_camera=np.eye(4).ravel().tolist()))
    return value


def test_preview_is_same_capture_bounded_and_budget_exhaustion_keeps_identity():
    value = observation()
    store = KeyframePreviews()
    assert store.add(value, 'kf-0000') == 'saved'
    entry = store.frames[0]
    assert entry['observationId'] == value.observation_id
    assert entry['stamp'] == value.wall_time_unix_ms
    for key, fmt in [('rgb', 'JPEG'), ('depth', 'PNG')]:
        raw = base64.b64decode(entry[key]['data'])
        assert hashlib.sha256(raw).hexdigest() == entry[key]['sha256']
        assert len(raw) == entry[key]['bytes']
        image = Image.open(io.BytesIO(raw))
        assert image.size == (240, 180)
        assert image.format == fmt
    rgb = np.asarray(Image.open(io.BytesIO(base64.b64decode(entry['rgb']['data']))))
    assert rgb[..., 0].mean() > 190 and rgb[..., 1:].mean() < 5
    denied = KeyframePreviews(max_bytes=1)
    assert denied.add(value, 'kf-0000') == 'budget_exhausted'
    assert denied.bytes == 0 and 'rgb' not in denied.frames[0]
    assert denied.frames[0]['observationId'] == value.observation_id


def test_metadata_snapshots_sensor_details_and_reports_final_pose_correction():
    slam = DenseSLAM()
    first = observation()
    assert slam.add(first)
    assert not slam.add(first)
    assert slam.add(observation(.2, 1001))
    assert len(slam.previews.frames) == 2
    slam.frames[1].pose = np.array([.23, .04, .05])
    source = slam.provenance()
    last = source['observations'][1]
    assert last['frameId'] == 'kf-0001' and last['pointCount'] > 100
    assert last['sourceId'] == 'unit/base-rgbd'
    assert last['cameraFrameId'] == 'base_optical'
    assert last['odometry'] == [.2, 0, 0]
    assert last['optimizedPose'] == [.23, .04, .05]
    assert last['correction']['translationM'] == pytest.approx(.05)
    assert last['correction']['yawRad'] == pytest.approx(.05)
    assert source['registrationAttempts'][0]['kind'] == 'adjacent'
    assert 'hidden_object_pose' not in json.dumps(source)
    first.rgbd_frame.intrinsics[0] = 20
    assert source['observations'][0]['intrinsics'][0] == 200


def test_saved_keyframe_artifact_is_manifest_verified_and_map_bound(tmp_path):
    slam = DenseSLAM()
    slam.add(observation())
    previews = slam.previews.document(map_id='test-map', robot_id='unit', calibration_revision='a'*64)
    manifest = build_map(tmp_path/'map', map_id='test-map', robot_id='unit', cloud=slam.cloud(),
        calibration_revision='a'*64, slam_metadata=slam.provenance(), slam_keyframes=previews)
    assert manifest['artifacts']['slam_keyframes']['bytes'] < 12*1024*1024
    assert all(check.ok for check in verify_artifacts(load_manifest(tmp_path/'map'), tmp_path/'map'))
    payload = tmp_path/'map'/manifest['artifacts']['slam_keyframes']['href']
    payload.write_bytes(payload.read_bytes()+b' ')
    assert not next(check for check in verify_artifacts(manifest, tmp_path/'map') if check.role == 'slam_keyframes').ok
    previews['mapId'] = 'other'
    with pytest.raises(ValueError, match='belong'):
        build_map(tmp_path/'wrong', map_id='test-map', robot_id='unit', cloud=slam.cloud(),
            calibration_revision='a'*64, slam_keyframes=previews)


def test_mask_statistics_preserve_depth_and_image_budget_is_session_bounded():
    value = observation()
    mask = np.zeros(value.rgbd_frame.width*value.rgbd_frame.height, np.uint8)
    mask[:500] = 1
    value.rgbd_frame.robot_self_mask = mask.tobytes()
    slam = DenseSLAM()
    slam.add(value)
    frame = slam.provenance()['observations'][0]
    assert frame['validDepthPixels']-frame['integratedDepthPixels'] == 500
    assert frame['selfMaskedPixels'] == 500
    assert slam.previews.document(map_id='m', robot_id='r', calibration_revision='a')['encoding']['rawDepthSaved'] is False
    slam.previews.frames = [{}]*400
    with pytest.raises(ValueError, match='count'):
        slam.previews.add(value, 'kf-0400')
