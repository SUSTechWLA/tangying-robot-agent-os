import itertools

import numpy as np
import pytest
from tangying_robot_gateway.grid_navigation import plan_grid_path, world_route
from tangying_robot_gateway.service_registry import ServiceError


def grid(cells):
    return {"width":cells.shape[1],"height":cells.shape[0],"cells":cells,"resolution":.1,"origin":[0.,0.,0.]}


def test_grid_navigation_uses_doorway_and_refuses_unknown_gap():
    cells=np.zeros((60,60),dtype=int)
    cells[:,30]=100;cells[30:44,30]=0
    path=plan_grid_path(grid(cells),[1.,1.],[5.,1.],radius=.25)
    assert max(point[1] for point in path)>3.
    cells[30:44,30]=-1
    with pytest.raises(ServiceError,match="通路"):
        plan_grid_path(grid(cells),[1.,1.],[5.,1.],radius=.25)


def test_grid_navigation_transforms_world_goal_without_changing_height():
    cells=np.zeros((80,80),dtype=int)
    start=[1.,1.,.035,1.,0.,0.,0.];goal=[2.,1.,.035,1.,0.,0.,0.]
    path=world_route(grid(cells),[.5,.3,.1],start,goal,.2)
    assert np.allclose(path[-1],goal)


@pytest.mark.parametrize("reverse",[False,True])
def test_exact_endpoints_cannot_hide_behind_a_clear_cell_center(reverse):
    cells=np.zeros((40,40),dtype=int);cells[13,14]=100
    start,goal=[1.25,1.75],[1.299,1.299]
    if reverse:start,goal=goal,start
    with pytest.raises(ServiceError,match="间距|通行空间"):
        plan_grid_path(grid(cells),start,goal,radius=.15)


def test_reached_goal_and_yaw_only_goal_never_detour_through_cell_center():
    import math
    cells=np.zeros((40,40),dtype=int)
    pose=[1.02,1.02,.035,1.,0.,0.,0.]
    for goal in (pose,[*pose[:3],math.cos(.2),0.,0.,math.sin(.2)]):
        assert np.allclose(world_route(grid(cells),[0.,0.,0.],pose,goal,.15),[goal])


@pytest.mark.parametrize("occupancy", [100, -1])
@pytest.mark.parametrize("direction", [(1, 0), (-1, 0), (0, 1), (0, -1)])
@pytest.mark.parametrize("gap", [-1e-7, 0., 1e-7])
def test_disk_contact_is_symmetric_at_exact_grid_boundaries(occupancy, direction, gap):
    cells = np.zeros((32, 32), dtype=int)
    cells[8, 8] = occupancy
    navigation_grid = {**grid(cells), "resolution": .125}
    # Binary-exact coordinates: the obstacle is [1, 1.125] on both axes.
    point = np.array([1.0625, 1.0625]) + np.array(direction) * (.0625 + .25 + gap)
    if gap <= 0:
        with pytest.raises(ServiceError):
            plan_grid_path(navigation_grid, point, point, radius=.25)
    else:
        assert np.allclose(plan_grid_path(navigation_grid, point, point, radius=.25), [point, point])


def _minimum_segment_box_distance(start, end, lower, upper):
    """Independent oracle: minimize the convex point-to-box distance in 1D."""
    from scipy.optimize import minimize_scalar

    start, end, lower, upper = (np.asarray(value) for value in (start, end, lower, upper))

    def squared_distance(fraction):
        point = start + fraction * (end - start)
        gap = np.maximum(np.maximum(lower - point, point - upper), 0.)
        return float(gap @ gap)

    optimum = minimize_scalar(squared_distance, bounds=(0., 1.), method="bounded",
                              options={"xatol": 1e-12})
    assert optimum.success
    return min(squared_distance(0.), squared_distance(1.), optimum.fun) ** .5


@pytest.mark.parametrize("origin", [[0., 0., 0.], [1.25, -2.5, .61]])
@pytest.mark.parametrize("occupancy", [100, -1])
@pytest.mark.parametrize("start,goal,straight_distance", [
    ([2., 4.], [6., 4.], 0.),  # Crosses the rectangle despite clear endpoints.
    ([2.5, 4.], [4., 2.], .2),  # Misses the rectangle but clips its rounded corner.
    ([2., 5.25], [6., 5.25], .25),  # Tangent at the middle of a horizontal segment.
])
def test_every_returned_segment_clears_rectangle_and_rounded_corners(
        origin, occupancy, start, goal, straight_distance):
    import math

    cells = np.zeros((64, 64), dtype=int)
    cells[24:40, 28:36] = occupancy
    navigation_grid = {**grid(cells), "resolution": .125, "origin": origin}
    lower, upper = [3.5, 3.], [4.5, 5.]
    assert _minimum_segment_box_distance(start, goal, lower, upper) == pytest.approx(straight_distance)
    cosine, sine = math.cos(origin[2]), math.sin(origin[2])
    rotation = np.array([[cosine, -sine], [sine, cosine]])

    def world(point):
        return rotation @ point + origin[:2]

    route = plan_grid_path(navigation_grid, world(start), world(goal), radius=.25)
    assert np.allclose(route[0], world(start))
    assert np.allclose(route[-1], world(goal))
    local_route = [(np.asarray(point) - origin[:2]) @ rotation for point in route]
    for first, second in itertools.pairwise(local_route):
        assert _minimum_segment_box_distance(first, second, lower, upper) > .25 + 1e-8


@pytest.mark.parametrize("reverse", [False, True])
def test_long_narrow_observed_strip_does_not_need_an_extra_sampling_margin(reverse):
    cells = np.full((48, 64), -1, dtype=int)
    cells[16:23, 4:60] = 0
    navigation_grid = {**grid(cells), "resolution": .125}
    start, goal = [1., 2.4375], [7., 2.4375]
    if reverse:
        start, goal = goal, start
    route = plan_grid_path(navigation_grid, start, goal, radius=.4)
    assert np.allclose(route[0], start)
    assert np.allclose(route[-1], goal)
    # The measured free rectangle is 0.875 m wide. Its centreline has only
    # 0.0375 m spare clearance beyond the footprint, independent of edge length.
    for x, y in route:
        assert min(x - .5, 7.5 - x, y - 2., 2.875 - y) > .4


@pytest.mark.parametrize("occupancy", [-1, 100])
@pytest.mark.parametrize("transpose", [False, True])
def test_exact_cell_clearance_preserves_narrow_certified_corridor(occupancy, transpose):
    # A 0.65 m measured corridor admits the unchanged 0.32 m chassis radius.
    # Subtracting a cell's half-diagonal from EDT incorrectly removes every
    # centreline node, although its exact side clearance is 0.325 m.
    cells = np.full((60, 100), occupancy, dtype=int)
    cells[20:33, 10:90] = 0
    start, goal = [1., 1.325], [4., 1.325]
    if transpose:
        cells = cells.T
        start, goal = start[::-1], goal[::-1]
    navigation_grid = {**grid(cells), "resolution": .05}
    route = plan_grid_path(navigation_grid, start, goal, radius=.32)
    assert np.allclose(route[0], start)
    assert np.allclose(route[-1], goal)
    for point in route:
        x, y = point[::-1] if transpose else point
        assert min(x-.5, 4.5-x, y-1., 1.65-y) > .32
    # Narrow one complete cross-section below the robot's diameter. Exact
    # clearance must still reject it for both unknown and occupied boundaries.
    if transpose:
        cells[50, 20] = occupancy
    else:
        cells[20, 50] = occupancy
    with pytest.raises(ServiceError, match="通路"):
        plan_grid_path(navigation_grid, start, goal, radius=.32)
