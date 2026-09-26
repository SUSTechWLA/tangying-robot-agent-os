import numpy as np
import pytest
from tangying_robot_gateway.dense_slam import DenseSLAM, Keyframe, relative


def biased_depth_graph(absolute_sigma):
    slam = DenseSLAM(odometry_sigma=(.001, .001, .001), absolute_odometry_sigma=absolute_sigma)
    for index in range(21):
        odom = np.array([index*.15, 0., 0.])
        slam.frames.append(Keyframe(np.zeros((1, 3)), np.zeros((1, 3), np.uint8),
            odom, odom+np.array([index*.001, 0., 0.]), index, str(index), 0., {}))
        if index:
            motion = relative(slam.frames[index-1].odometry, odom)
            slam.edges.extend([
                (index-1, index, motion, np.eye(3)*1000, "odometry"),
                (index-1, index, motion+np.array([.008, 0., 0.]), np.eye(3)*250, "depth_icp")])
    slam.optimize()
    return slam


def test_absolute_measurements_prevent_accumulating_depth_bias_without_changing_relative_default():
    relative_only = biased_depth_graph(None)
    anchored = biased_depth_graph((.001, .001, .001))
    assert abs(relative_only.frames[-1].pose[0]-3.) > .003
    assert max(np.linalg.norm(f.pose[:2]-f.odometry[:2]) for f in anchored.frames) < .002
    assert relative_only.provenance()["absoluteOdometrySigma"] is None
    assert anchored.provenance()["absoluteOdometrySigma"] == [.001, .001, .001]


@pytest.mark.parametrize("sigma", [(0., .001, .001), (.001, float("nan"), .001), (.001, .001)])
def test_absolute_pose_requires_explicit_valid_covariance(sigma):
    with pytest.raises(ValueError, match="absolute odometry sigma"):
        DenseSLAM(absolute_odometry_sigma=sigma)
