"""Conservative map-grid planning; robot drivers still check live obstacles."""
import heapq
import itertools
import math

import numpy as np
from scipy.ndimage import distance_transform_edt

from .dense_slam import compose, pose_se2, relative
from .navigation_map import validate_grid
from .service_registry import ServiceError


def plan_grid_path(grid, start, goal, *, radius):
    grid=validate_grid(grid)
    if not math.isfinite(radius) or radius <= 0 or any(not math.isfinite(v) for v in (*start[:2],*goal[:2])):
        raise ServiceError("INVALID_POSE","导航位置与底盘半径必须是有限有效值。")
    start,goal=list(start[:2]),list(goal[:2])
    if grid["width"]*grid["height"]>250000:
        raise ServiceError("MAP_TOO_LARGE","地图超过当前规划器预算，请使用注册的 Nav2 服务。")
    resolution=grid["resolution"]
    ox,oy,yaw=grid["origin"]
    c,s=math.cos(yaw),math.sin(yaw)
    def cell(point):
        x,y=point[0]-ox,point[1]-oy
        return math.floor((-s*x+c*y)/resolution),math.floor((c*x+s*y)/resolution)
    def world(node):
        y,x=(node[0]+.5)*resolution,(node[1]+.5)*resolution
        return [ox+c*x-s*y,oy+s*x+c*y]
    def local(point):
        x,y=point[0]-ox,point[1]-oy
        return np.array([c*x+s*y,-s*x+c*y])
    def point_clear(point, clearance_radius):
        x,y=local(point)
        if min(x,y,grid["width"]*resolution-x,grid["height"]*resolution-y)<=clearance_radius:
            return False
        left,bottom=np.maximum(np.floor((np.array([x,y])-clearance_radius)/resolution).astype(int)-1,0)
        right,top=np.minimum(np.floor((np.array([x,y])+clearance_radius)/resolution).astype(int)+1,
                             [grid["width"]-1,grid["height"]-1])
        rows,cols=np.nonzero(grid["cells"][bottom:top+1,left:right+1]!=0)
        dx=np.maximum(np.maximum((cols+left)*resolution-x,x-(cols+left+1)*resolution),0)
        dy=np.maximum(np.maximum((rows+bottom)*resolution-y,y-(rows+bottom+1)*resolution),0)
        return not np.any(dx*dx+dy*dy<=clearance_radius*clearance_radius)
    def segment_clear(start_point,end_point):
        a,b=local(start_point),local(end_point)
        if not point_clear(start_point,radius) or not point_clear(end_point,radius):return False
        delta=b-a
        length_squared=float(delta@delta)
        if length_squared<=1e-20:return True
        low=np.maximum(np.floor((np.minimum(a,b)-radius)/resolution).astype(int)-1,0)
        high=np.minimum(np.floor((np.maximum(a,b)+radius)/resolution).astype(int)+1,
                        [grid["width"]-1,grid["height"]-1])
        rows,cols=np.nonzero(grid["cells"][low[1]:high[1]+1,low[0]:high[0]+1]!=0)
        if not len(rows):return True
        boxes=np.stack([cols+low[0],rows+low[1]],axis=1)*resolution
        upper=boxes+resolution
        # Exact capsule/AABB distance: crossing the rectangle, endpoint-to-box,
        # and rectangle-corner-to-segment cover every possible closest pair.
        enter,leave=np.zeros(len(boxes)),np.ones(len(boxes))
        for axis in (0,1):
            if abs(delta[axis])<1e-15:
                outside=(a[axis]<boxes[:,axis])|(a[axis]>upper[:,axis])
                enter[outside]=np.inf
            else:
                first=(boxes[:,axis]-a[axis])/delta[axis]
                second=(upper[:,axis]-a[axis])/delta[axis]
                enter=np.maximum(enter,np.minimum(first,second))
                leave=np.minimum(leave,np.maximum(first,second))
        if np.any(enter<=leave):return False
        for endpoint in (a,b):
            gap=np.maximum(np.maximum(boxes-endpoint,endpoint-upper),0)
            if np.any(np.sum(gap*gap,axis=1)<=radius*radius):return False
        for offset in ((0,0),(0,1),(1,0),(1,1)):
            corner=boxes+np.array(offset)*resolution
            fraction=np.clip((corner-a)@delta/length_squared,0,1)
            gap=corner-a-fraction[:,None]*delta
            if np.any(np.sum(gap*gap,axis=1)<=radius*radius):return False
        return True
    if math.dist(start,goal)<=1e-9 and point_clear(start,radius):
        return [start,goal]  # A yaw-only goal must not add a translation detour.
    # Padding is occupied, including the map's outer edge.
    clearance=distance_transform_edt(np.pad(grid["cells"]==0,1,constant_values=False))[1:-1,1:-1]*resolution
    free=clearance>radius+resolution/math.sqrt(2)
    def valid(node):
        return 0<=node[0]<grid["height"] and 0<=node[1]<grid["width"] and free[node]
    def connect(point):
        row,col=cell(point)
        candidates=[(row+dy,col+dx) for dy in range(-2,3) for dx in range(-2,3)]
        # Endpoint connectors should favour corridor clearance too. Choosing
        # only the nearest cell may introduce a needless graze beside a table.
        def connector_cost(node):
            if not valid(node):return math.inf
            preferred=radius+.12
            available=max(0.,clearance[node]-resolution/math.sqrt(2))
            return math.dist(point,world(node))+max(0.,preferred-available)*2
        return [node for node in sorted(candidates,key=connector_cost)
                if valid(node) and segment_clear(point,world(node))]
    sources,targets=connect(start),set(connect(goal))
    if not sources or not point_clear(start,radius):
        raise ServiceError("LOCALIZATION_NOT_CLEAR","当前底盘位置没有足够已知通行空间，请重新定位或补扫周围。")
    if not targets or not point_clear(goal,radius):
        raise ServiceError("GOAL_NOT_CLEAR","目标工作区尚未扫描，或底盘安全间距不足，请补扫或选择其他位置。")
    def h(node): return min(math.hypot(node[0]-target[0],node[1]-target[1]) for target in targets)
    # Multiple certified connectors avoid forcing a small detour through the
    # nearest grid centre at a precisely commissioned working pose.
    cost={source:math.dist(start,world(source))/resolution for source in sources}
    parent={source:None for source in sources}
    frontier=[(h(source)+distance,distance,source) for source,distance in cost.items()]
    heapq.heapify(frontier)
    target=None
    while frontier:
        _,distance,node=heapq.heappop(frontier)
        if distance!=cost[node]:continue
        if node in targets:
            target=node
            break
        for dy,dx in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1)):
            neighbor=node[0]+dy,node[1]+dx
            if not valid(neighbor):continue
            if dx and dy and (not valid((node[0]+dy,node[1])) or not valid((node[0],node[1]+dx))):continue
            # Search only edges whose complete swept disk is clear. A cell
            # centre can be clear while the segment beside a corner is not.
            if not segment_clear(world(node),world(neighbor)):continue
            # Prefer the middle of observed corridors instead of grazing the
            # minimum footprint clearance to save a few centimetres of travel.
            # Live controllers can require more room than the map's hard bound.
            preferred=radius+.12
            available=max(0.,clearance[neighbor]-resolution/math.sqrt(2))
            penalty=8*(max(0.,preferred-available)/preferred)**2
            candidate=distance+math.hypot(dx,dy)*(1+penalty)
            if candidate<cost.get(neighbor,math.inf):
                cost[neighbor],parent[neighbor]=candidate,node
                heapq.heappush(frontier,(candidate+h(neighbor),candidate,neighbor))
    if target not in parent:
        raise ServiceError("NO_KNOWN_PATH","当前地图没有连接到目标工作区的通路，请补扫连接区域。")
    path,node=[],target
    while node is not None:
        path.append(node);node=parent[node]
    path.reverse()
    # Keep the certified edges. Compressing them changes the sampling margin
    # and may reject an otherwise valid route through a narrow observed strip.
    result=[list(start[:2]),*[world(node) for node in path],list(goal[:2])]
    if any(not segment_clear(a,b) for a,b in itertools.pairwise(result)):
        raise ServiceError("PATH_CLEARANCE_INSUFFICIENT","连续路径的底盘安全间距不足，请补扫或调整工作区。")
    return result


def world_route(grid, anchor, start_pose, goal_pose, radius):
    start,goal=compose(anchor,pose_se2(start_pose)),compose(anchor,pose_se2(goal_pose))
    path=plan_grid_path(grid,start,goal,radius=radius)
    inverse=relative(anchor,np.zeros(3))
    result=[]
    for point in path[1:]:
        pose=compose(inverse,[point[0],point[1],goal[2]])
        result.append([float(pose[0]),float(pose[1]),goal_pose[2],math.cos(pose[2]/2),0.,0.,math.sin(pose[2]/2)])
    return result
