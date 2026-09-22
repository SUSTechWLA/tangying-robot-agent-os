import numpy as np
from tangying_sim.rgbd_perception import colour_clusters


def fragments(second_x=0., second_z=.98):
    mask = np.zeros((12, 8), dtype=bool)
    mask[0:4, 0:4] = mask[8:12, 0:4] = True
    points = np.zeros((*mask.shape, 3))
    grid_y, grid_x = np.mgrid[0:4, 0:4]*.012
    points[0:4, 0:4, 0] = grid_x
    points[0:4, 0:4, 1] = grid_y
    points[0:4, 0:4, 2] = .84 + grid_y
    points[8:12, 0:4, 0] = grid_x+second_x
    points[8:12, 0:4, 1] = grid_y
    points[8:12, 0:4, 2] = second_z
    return mask, points


def test_occluded_cap_and_body_fit_one_commissioned_bottle():
    mask, points = fragments()
    assert len(colour_clusters(mask, points)) == 2
    assert len(colour_clusters(mask, points, object_height_m=.16)) == 1


def test_distinct_or_overheight_fragments_keep_ambiguous_identity():
    for x, z in ((.09, .98), (0., 1.08)):
        mask, points = fragments(x, z)
        assert len(colour_clusters(mask, points, object_height_m=.16)) == 2
