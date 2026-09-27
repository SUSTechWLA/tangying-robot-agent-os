"""Map-derived places: measured geometry, explicit names, certified base goals.

Room names are annotations, never inferred from simulator meshes. Every usable
goal is checked against the same measured-free grid used for motion admission.
Work areas retain their commissioned pose: snapping a manipulation dock changes
reach and needs a new commissioning, whereas a room seed may move a bounded amount.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from .contracts import validate_pose
from .dense_slam import compose, pose_se2, relative
from .grid_navigation import pose_is_clear
from .navigation_map import validate_grid
from .semantic_map import normalize_location_name

MAX_LOCATIONS = 256


def certify_locations(grid, annotations, *, radius, include_regions=False):
    grid = validate_grid(grid)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("semantic footprint radius must be positive and finite")
    if not isinstance(annotations, list) or len(annotations) > MAX_LOCATIONS:
        raise ValueError("semantic locations exceed the bounded catalogue")
    result, labels = [], {}
    for original in annotations:
        item = copy.deepcopy(original)
        name = item.get("name")
        aliases = item.get("aliases", [])
        if (not isinstance(name, str) or not name.strip() or len(name.encode()) > 256
                or not isinstance(aliases, list) or len(aliases) > 32
                or any(not isinstance(a, str) or not a.strip() or len(a.encode()) > 256 for a in aliases)):
            raise ValueError("semantic location names and aliases must be bounded strings")
        for label in [name, *aliases]:
            key = normalize_location_name(label)
            if key in labels and labels[key] != name:
                raise ValueError("semantic location alias is ambiguous")
            labels[key] = name
        if any(existing["name"] == name for existing in result):
            raise ValueError("duplicate semantic location")
        if item.get("kind","room") not in {"room","work_area","region"}:
            raise ValueError("unknown semantic location kind")
        if sum(len(existing.get("aliases",[])) for existing in result)+len(aliases)>512:
            raise ValueError("semantic aliases exceed navigation catalogue budget")
        pose = validate_pose(item.get("navigationPose", []))
        clear = pose_is_clear(grid, pose, radius)
        # A fixed work pose must not silently drift away from its object.
        if not clear and item.get("kind", "room") == "room":
            pose = _nearby_clear_pose(grid, pose, radius) or pose
            clear = pose_is_clear(grid, pose, radius)
        item.update(navigationPose=list(pose), kind=item.get("kind", "room"),
                    navigationReady=clear, blockers=[] if clear else ["SEMANTIC_GOAL_NOT_CLEAR"],
                    geometrySource="measured_navigation_grid", footprintRadiusM=float(radius))
        result.append(item)
    if include_regions and abs(grid["origin"][2]) < 1e-9 and grid["width"]*grid["height"]<=250_000:
        from .room_segmentation import segment_rooms
        segmentation = segment_rooms(grid["cells"], resolution=grid["resolution"],
            origin=grid["origin"][:2], footprint_radius_m=radius,
            door_radius_m=.45, min_room_area_m2=.8)
        for item in result:
            room = segmentation.room_of(*item["navigationPose"][:2])
            if room is not None:
                item.update(regionId=f"region-{room.label}", measuredRegion=room.to_dict())
        # Unknown rooms stay numbered. A geometric region cannot name itself kitchen.
        for room in segmentation.rooms:
            name = f"region-{room.label}"
            region_alias = f"区域{room.label}"
            if (normalize_location_name(name) in labels or normalize_location_name(region_alias) in labels
                    or len(result) >= MAX_LOCATIONS
                    or sum(len(item.get("aliases",[])) for item in result)>=512):
                continue
            x, y = room.centre_xy
            pose = [x, y, 0., 1., 0., 0., 0.]
            clear = pose_is_clear(grid, pose, radius)
            result.append({"name":name, "aliases":[region_alias], "kind":"region",
                "target":[x,y,0.], "navigationPose":pose, "annotationSource":"slam_region_unlabelled",
                "navigationReady":clear, "blockers":[] if clear else ["SEMANTIC_GOAL_NOT_CLEAR"],
                "geometrySource":"measured_navigation_grid", "footprintRadiusM":float(radius),
                "regionId":name, "measuredRegion":room.to_dict()})
    return result


def _nearby_clear_pose(grid, pose, radius, maximum=.25):
    """Bounded adjustment around an annotated room seed; never a nearest-room guess."""
    ox, oy, yaw = grid["origin"]
    c, s, resolution = math.cos(yaw), math.sin(yaw), grid["resolution"]
    dx, dy = pose[0]-ox, pose[1]-oy
    col, row = math.floor((c*dx+s*dy)/resolution), math.floor((-s*dx+c*dy)/resolution)
    count = math.ceil(maximum/resolution)
    candidates = []
    for r in range(max(0,row-count), min(grid["height"],row+count+1)):
        for k in range(max(0,col-count), min(grid["width"],col+count+1)):
            x, y = (k+.5)*resolution, (r+.5)*resolution
            p = [ox+c*x-s*y, oy+s*x+c*y, *pose[2:]]
            distance = math.dist(p[:2],pose[:2])
            if distance <= maximum:
                candidates.append((distance,r,k,p))
    for _, _, _, candidate in sorted(candidates):
        if pose_is_clear(grid, candidate, radius):
            return candidate
    return None


def navigation_contract(active, locations, map_from_world, *, robot_id):
    """Publish saved map poses in world, binding the exact activated map revision."""
    inverse = relative(np.asarray(map_from_world,dtype=float),np.zeros(3))
    goals, aliases, blocked = {}, {}, []
    for item in locations:
        if not item["navigationReady"]:
            blocked.append({"name":item["name"],"blockers":list(item["blockers"])})
            continue
        pose = item["navigationPose"]
        point = compose(inverse,pose_se2(pose))
        goals[item["name"]] = [float(point[0]),float(point[1]),pose[2],
                                math.cos(point[2]/2),0.,0.,math.sin(point[2]/2)]
        for label in item.get("aliases", []):
            aliases[label] = item["name"]
    return {"schemaVersion":"semantic.navigation.v1", "frameId":"world", "robotId":robot_id,
            **active, "goals":goals, "aliases":aliases, "blockedLocations":blocked,
            "goalSource":"saved_slam_semantics"}
