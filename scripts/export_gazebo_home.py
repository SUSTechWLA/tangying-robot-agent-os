"""Export the commissioned XLeRobot home; MuJoCo is a build-time dependency only.

Compiled mesh coordinates include MuJoCo's principal-axis transform. Exporting
those vertices with the compiled geom pose avoids applying that transform twice.
Runtime consumers use only the generated SDF and commissioning JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for area in ('sim/mujoco', 'robot/gateway', 'python'):
    sys.path.insert(0, str(ROOT / area))
import mujoco
import numpy as np
from PIL import Image
from tangying_robot_gateway.arm_kinematics import all_links
from tangying_sim.home_scene import HOME_WAYPOINTS
from tangying_sim.rgbd_navigation import load_navigation_model


def numbers(values):
    return ' '.join(f'{float(v):.12g}' for v in values)


def matrix(pos, quat):
    rot = np.empty(9); mujoco.mju_quat2Mat(rot, np.asarray(quat, dtype=float))
    out = np.eye(4); out[:3, :3] = rot.reshape(3, 3); out[:3, 3] = pos
    return out


def pose(parent, transform, **kwargs):
    r = transform[:3, :3]
    pitch = math.asin(np.clip(-r[2, 0], -1, 1))
    roll = math.atan2(r[2, 1], r[2, 2]); yaw = math.atan2(r[1, 0], r[0, 0])
    ET.SubElement(parent, 'pose', **kwargs).text = numbers([*transform[:3, 3], roll, pitch, yaw])


def item(parent, tag, value):
    ET.SubElement(parent, tag).text = str(value)


def plugin(parent, name, filename, **values):
    p = ET.SubElement(parent, 'plugin', name=name, filename=filename)
    for key, value in values.items(): item(p, key, value)
    return p


def export(output: Path, pack: Path):
    os.environ['TANGYING_HOME_ASSET_PACK'] = str(pack.resolve())
    model = load_navigation_model(scene='home_task')
    model.cam_quat[model.camera('head_depth').id] = [.668536, .230346, -.230346, -.668536]
    data = mujoco.MjData(model); mujoco.mj_forward(model, data)
    output.mkdir(parents=True, exist_ok=True)
    meshes = output / 'meshes'; meshes.mkdir(exist_ok=True)
    textures = output / 'textures'; textures.mkdir(exist_ok=True)
    # The mount is fixed for commissioning, just as in the reference runtime.
    body_matrix = [matrix(data.xpos[i], data.xquat[i]) for i in range(model.nbody)]
    chassis = model.body('chassis').id
    robot_bodies = {chassis}
    for b in range(chassis + 1, model.nbody):
        if int(model.body_parentid[b]) in robot_bodies: robot_bodies.add(b)
    logical = {model.body(link.body).id: link for link in all_links()}
    roots = {chassis} | {b for b in robot_bodies if b != chassis and model.body_jntnum[b]}
    group = {}
    for b in sorted(robot_bodies):
        group[b] = b if b in roots else group[int(model.body_parentid[b])]
    names = {b: ('base_link' if b == chassis else logical[b].link if b in logical else model.body(b).name)
             for b in roots}
    root = ET.Element('sdf', version='1.9'); world = ET.SubElement(root, 'world', name='tangying_home')
    physics = ET.SubElement(world, 'physics', name='physics', type='ignored')
    item(physics, 'max_step_size', .002); item(physics, 'real_time_factor', 1)
    item(world, 'gravity', '0 0 -9.81')
    for name, filename in [('Physics', 'gz-sim-physics-system'), ('UserCommands', 'gz-sim-user-commands-system'),
                           ('SceneBroadcaster', 'gz-sim-scene-broadcaster-system'), ('Imu', 'gz-sim-imu-system')]:
        plugin(world, 'gz::sim::systems::'+name, filename)
    plugin(world, 'gz::sim::systems::Sensors', 'gz-sim-sensors-system', render_engine='ogre2')
    light = ET.SubElement(world, 'light', name='sun', type='directional')
    pose(light, matrix([0,0,10], [1,0,0,0])); item(light, 'diffuse', '.85 .85 .85 1')
    item(light, 'specular', '.2 .2 .2 1'); item(light, 'direction', '-.2 -.3 -1'); item(light, 'cast_shadows', 'false')
    scene = ET.SubElement(world, 'scene'); item(scene, 'ambient', '.5 .5 .5 1'); item(scene, 'background', '.7 .8 .9 1')
    robot = ET.SubElement(world, 'model', name='tangying_robot', canonical_link='base_link')
    initial = body_matrix[chassis].copy(); initial[:2, 3] = HOME_WAYPOINTS['living_room'][:2]
    pose(robot, initial); item(robot, 'self_collide', 'false')
    mesh_files = {}; tex_files = {}
    for mid in range(model.nmesh):
        va, vn = model.mesh_vertadr[mid], model.mesh_vertnum[mid]
        fa, fn = model.mesh_faceadr[mid], model.mesh_facenum[mid]
        ta, tn = model.mesh_texcoordadr[mid], model.mesh_texcoordnum[mid]
        lines = [f'v {numbers(v)}' for v in model.mesh_vert[va:va+vn]]
        if tn: lines += [f'vt {numbers(t)}' for t in model.mesh_texcoord[ta:ta+tn]]
        na, nn = model.mesh_normaladr[mid], model.mesh_normalnum[mid]
        lines += [f'vn {numbers(n)}' for n in model.mesh_normal[na:na+nn]]
        for face, uv, normals in zip(model.mesh_face[fa:fa+fn], model.mesh_facetexcoord[fa:fa+fn], model.mesh_facenormal[fa:fa+fn]):
            lines.append('f '+' '.join(f'{v+1}/{t+1}/{n+1}' if tn and t>=0 else f'{v+1}//{n+1}' for v,t,n in zip(face,uv,normals)))
        path = meshes / f'mesh-{mid}.obj'; path.write_text('\n'.join(lines)+'\n')
        mesh_files[mid] = f'/assets/xlerobot-home/meshes/{path.name}'
    for tid in range(model.ntex):
        if int(model.tex_type[tid]) != int(mujoco.mjtTexture.mjTEXTURE_2D): continue
        h,w,c = int(model.tex_height[tid]),int(model.tex_width[tid]),int(model.tex_nchannel[tid])
        adr = model.tex_adr[tid]
        pixels = model.tex_data[adr:adr+h*w*c].reshape(h,w,c)
        path = textures / f'texture-{tid}.png'; Image.fromarray(pixels.squeeze() if c==1 else pixels).save(path)
        tex_files[tid] = f'/assets/xlerobot-home/textures/{path.name}'

    def geometry(part, gid):
        geo = ET.SubElement(part, 'geometry'); size = model.geom_size[gid]; kind = int(model.geom_type[gid])
        if kind == int(mujoco.mjtGeom.mjGEOM_BOX): item(ET.SubElement(geo, 'box'), 'size', numbers(size*2))
        elif kind == int(mujoco.mjtGeom.mjGEOM_PLANE):
            p = ET.SubElement(geo, 'plane'); item(p, 'normal', '0 0 1'); item(p, 'size', '40 40')
        elif kind == int(mujoco.mjtGeom.mjGEOM_CYLINDER):
            p=ET.SubElement(geo, 'cylinder'); item(p,'radius',size[0]); item(p,'length',size[1]*2)
        elif kind == int(mujoco.mjtGeom.mjGEOM_SPHERE): item(ET.SubElement(geo,'sphere'),'radius',size[0])
        elif kind == int(mujoco.mjtGeom.mjGEOM_MESH): item(ET.SubElement(geo,'mesh'),'uri',mesh_files[int(model.geom_dataid[gid])])
        elif kind == int(mujoco.mjtGeom.mjGEOM_CAPSULE):
            p=ET.SubElement(geo,'capsule'); item(p,'radius',size[0]); item(p,'length',size[1]*2)
        else: raise ValueError(f'unsupported geometry {kind}: {model.geom(gid).name}')

    def add_geoms(link, bodies, origin):
        for gid in range(model.ngeom):
            b = int(model.geom_bodyid[gid])
            if b not in bodies: continue
            t = np.linalg.inv(origin) @ body_matrix[b] @ matrix(model.geom_pos[gid], model.geom_quat[gid])
            collision = bool(model.geom_contype[gid] or model.geom_conaffinity[gid])
            for kind in (['collision','visual'] if collision else ['visual']):
                part = ET.SubElement(link,kind,name=f'geom-{gid}'); pose(part,t); geometry(part,gid)
                if kind=='collision':
                    fr = ET.SubElement(ET.SubElement(ET.SubElement(part,'surface'),'friction'),'ode')
                    item(fr,'mu', model.geom_friction[gid,0]); item(fr,'mu2',model.geom_friction[gid,0])
                else:
                    mat = ET.SubElement(part,'material'); rgba=model.geom_rgba[gid]; mi=int(model.geom_matid[gid])
                    if mi>=0: rgba=model.mat_rgba[mi]
                    item(mat,'ambient',numbers(rgba)); item(mat,'diffuse',numbers(rgba))
                    if mi>=0:
                        tid=int(model.mat_texid[mi,int(mujoco.mjtTextureRole.mjTEXROLE_RGB)])
                        if tid in tex_files:
                            metal=ET.SubElement(ET.SubElement(mat,'pbr'),'metal')
                            item(metal,'albedo_map',tex_files[tid]); item(metal,'roughness',.85); item(metal,'metalness',0)

    def inertia(link,bodies,origin):
        total = sum(float(model.body_mass[b]) for b in bodies)
        if total<=0: return
        com = np.zeros(3); poses={}
        for b in bodies:
            poses[b]=np.linalg.inv(origin)@body_matrix[b]@matrix(model.body_ipos[b],model.body_iquat[b])
            com += model.body_mass[b]*poses[b][:3,3]
        com/=total; tensor=np.zeros((3,3))
        for b in bodies:
            mass=model.body_mass[b]; r=poses[b][:3,:3]; delta=poses[b][:3,3]-com
            tensor += r@np.diag(model.body_inertia[b])@r.T + mass*(np.eye(3)*(delta@delta)-np.outer(delta,delta))
        inert=ET.SubElement(link,'inertial');pose(inert,matrix(com,[1,0,0,0]));item(inert,'mass',total)
        values=ET.SubElement(inert,'inertia')
        for name,(i,j) in {'ixx':(0,0),'iyy':(1,1),'izz':(2,2),'ixy':(0,1),'ixz':(0,2),'iyz':(1,2)}.items(): item(values,name,tensor[i,j])

    for b in sorted(roots):
        link=ET.SubElement(robot,'link',name=names[b]);pose(link,np.linalg.inv(body_matrix[chassis])@body_matrix[b])
        members={i for i in robot_bodies if group[i]==b}
        inertia(link,members,body_matrix[b]);add_geoms(link,members,body_matrix[b])
        if b==chassis:
            imu=ET.SubElement(link,'sensor',name='imu',type='imu');item(imu,'topic','/imu');item(imu,'update_rate',50);item(imu,'always_on','true')
        else:
            jid=int(model.body_jntadr[b]);jname=logical[b].motor if b in logical else model.joint(jid).name
            joint=ET.SubElement(robot,'joint',name=jname,type='revolute');item(joint,'parent',names[group[int(model.body_parentid[b])]])
            item(joint,'child',names[b]);pose(joint,matrix(model.jnt_pos[jid],[1,0,0,0]),relative_to=names[b])
            axis=ET.SubElement(joint,'axis');item(axis,'xyz',numbers(model.jnt_axis[jid]));limits=ET.SubElement(axis,'limit')
            if model.jnt_limited[jid]:item(limits,'lower',model.jnt_range[jid,0]);item(limits,'upper',model.jnt_range[jid,1])
            item(limits,'effort',20);item(limits,'velocity',2)
            dynamics=ET.SubElement(axis,'dynamics');item(dynamics,'damping',.1)
            if b in logical or model.body(b).name.startswith("head_"):
                plugin(robot,'gz::sim::systems::JointPositionController','gz-sim-joint-position-controller-system',joint_name=jname,
                       topic=f'/joint/{jname}/cmd_pos',p_gain=10 if b in logical and logical[b].index==6 else 80,i_gain=2,d_gain=.04 if b in logical and logical[b].index==6 else .15,i_max=1,i_min=-1,cmd_max=20,cmd_min=-20)
    for b in range(model.nbody):
        if b in robot_bodies: continue
        members={b};gids=np.flatnonzero(model.geom_bodyid==b)
        if not len(gids):continue
        obj=ET.SubElement(world,'model',name='world_geometry' if b==0 else model.body(b).name or f'body-{b}')
        pose(obj,body_matrix[b]);item(obj,'static','false' if model.body_jntnum[b] else 'true')
        link=ET.SubElement(obj,'link',name='body');inertia(link,members,body_matrix[b]);add_geoms(link,members,body_matrix[b])
    # Match the reference model's planar joints; avoid free-base roll, pitch
    # and wheel-contact drift that the commissioned MuJoCo prototype excludes.
    for name in ('base_slide_x_link','base_slide_y_link'):
        link=ET.SubElement(robot,'link',name=name)
        inert=ET.SubElement(link,'inertial');item(inert,'mass',.001)
        tensor=ET.SubElement(inert,'inertia')
        for key in ('ixx','iyy','izz'): item(tensor,key,.000001)
    for name,parent,child,axis,kind in (
        ('base_slide_x','world','base_slide_x_link','1 0 0','prismatic'),
        ('base_slide_y','base_slide_x_link','base_slide_y_link','0 1 0','prismatic'),
        ('base_yaw','base_slide_y_link','base_link','0 0 1','revolute')):
        joint=ET.SubElement(robot,'joint',name=name,type=kind)
        item(joint,'parent',parent);item(joint,'child',child)
        ax=ET.SubElement(joint,'axis');ET.SubElement(ax,'xyz',expressed_in='__model__').text=axis
        lim=ET.SubElement(ax,'limit');item(lim,'effort',1000);item(lim,'velocity',1)
        if kind=='prismatic':item(lim,'lower',-20);item(lim,'upper',20)
    cameras={}
    optical_from_link=np.array([[0,0,1],[-1,0,0],[0,-1,0]])
    base_link=robot.find("link[@name='base_link']")
    for name,mjname in [('base','base_depth'),('head','head_depth')]:
        cid=model.camera(mjname).id;cb=int(model.cam_bodyid[cid])
        # MuJoCo camera is right/up/back, Runtime optical is right/down/front.
        optical=np.linalg.inv(body_matrix[chassis])@body_matrix[cb]@matrix(model.cam_pos[cid],model.cam_quat[cid])@np.diag([1,-1,-1,1])
        mount=optical.copy();mount[:3,:3]=optical[:3,:3]@optical_from_link.T
        sensor=ET.SubElement(base_link,'sensor',name=f'{name}_rgbd',type='rgbd_camera');pose(sensor,mount)
        item(sensor,'topic',f'/camera/{name}');item(sensor,'gz_frame_id',f'{name}_camera_optical_frame')
        item(sensor,'always_on','true');item(sensor,'update_rate',10)
        camera=ET.SubElement(sensor,'camera');hfov=2*math.atan(math.tan(math.radians(model.cam_fovy[cid])/2)*4/3)
        item(camera,'horizontal_fov',hfov);image=ET.SubElement(camera,'image');item(image,'width',320);item(image,'height',240);item(image,'format','R8G8B8')
        clip=ET.SubElement(camera,'clip');item(clip,'near',.03);item(clip,'far',10)
        depth=ET.SubElement(camera,'depth_camera');dc=ET.SubElement(depth,'clip');item(dc,'near',.03);item(dc,'far',10)
        cameras[name+'-rgbd']={'baseFromOptical':optical.tolist(),'baseFromLink':mount.tolist(),'fovy':float(model.cam_fovy[cid]),'width':320,'height':240}
    plugin(robot,'tangying::PlanarDrive','libtangying_suction.so')
    plugin(robot,'gz::sim::systems::OdometryPublisher','gz-sim-odometry-publisher-system',odom_topic='/odom',
           tf_topic='/model/tangying_robot/tf',odom_frame='odom',robot_base_frame='base_link',dimensions=3,odom_publish_frequency=30)
    plugin(robot,'gz::sim::systems::JointStatePublisher','gz-sim-joint-state-publisher-system',topic='/joint_states',update_rate=30)
    suction=plugin(robot,'tangying::Suction','libtangying_suction.so')
    for obj,pickable in [('ceramic_mug',True),('kitchen_tray',False)]:
        ET.SubElement(suction,'object',name=obj,pickable=str(pickable).lower())
    stow=plugin(robot,'tangying::InitialJointPose','libtangying_suction.so')
    for link in all_links():
        value=(0,3.1,1,0,0,0)[link.index-1]
        ET.SubElement(stow,'joint',name=link.motor,position=str(value))
        p=robot.find(f"plugin[joint_name='{link.motor}']")
        if p is not None: item(p,'initial_position',value)
    for name in ('head_pan_joint','head_tilt_joint'):
        ET.SubElement(stow,'joint',name=name,position='0')
    ET.indent(root,space='  ');ET.ElementTree(root).write(output/'home.sdf',encoding='utf-8',xml_declaration=True)
    source_hash=hashlib.sha256()
    for area in ['sim/mujoco/assets','sim/mujoco/tangying_sim','scripts/export_gazebo_home.py']:
        path=ROOT/area
        for file in sorted(path.rglob('*')) if path.is_dir() else [path]:
            if file.is_file() and '__pycache__' not in file.parts:
                source_hash.update(str(file.relative_to(ROOT)).encode());source_hash.update(file.read_bytes())
    metadata={'schemaVersion':'robot.home.commissioning.v1','robotModel':'xlerobot','scene':'home_task','sourceRevision':source_hash.hexdigest(),
              'worldRevision':hashlib.sha256((output/'home.sdf').read_bytes()).hexdigest(),'cameras':cameras,'waypoints':HOME_WAYPOINTS,
              'geometryCount':model.ngeom,'robotLinkCount':len(roots),'upstreamManifestSha256':hashlib.sha256((pack/'manifest.json').read_bytes()).hexdigest(),
              'baseDrive':'bounded_planar_velocity','graspMode':'sim_suction'}
    metadata['files'] = {str(path.relative_to(output)):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob('*')) if path.is_file() and path.name != 'commissioning.json'}
    metadata['resourceRevision'] = hashlib.sha256(json.dumps(metadata,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    (output/'commissioning.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps({'output':str(output),'worldRevision':metadata['worldRevision'],'links':len(roots),'geoms':model.ngeom}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'artifacts/sim-assets/xlerobot-home')
    parser.add_argument('--pack',type=Path,default=ROOT/'artifacts/sim-assets/furnished-home');args=parser.parse_args();export(args.output,args.pack)
