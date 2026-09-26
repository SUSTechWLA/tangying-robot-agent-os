"""Exclude measured robot surfaces using CAD and capture-time encoders.

Only the selected robot's visual geometry is loaded from its SDF description.
No environment geometry, object poses, simulator queries or segmentation IDs
are used. A foreground object inside a link's bounding box remains visible:
its measured endpoint must agree with the actual CAD surface within 4 mm.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


def sdf_pose(element):
    values = np.fromstring(element.text, sep=' ') if element is not None and element.text else np.zeros(6)
    if values.shape != (6,) or not np.isfinite(values).all():
        raise ValueError('SELF_FILTER_CAD_POSE_INVALID')
    out = np.eye(4)
    out[:3, :3] = Rotation.from_euler('xyz', values[3:]).as_matrix()
    out[:3, 3] = values[:3]
    return out


class CadSelfFilter:
    def __init__(self, description, asset_root, *, robot_name='tangying_robot', revision):
        import trimesh
        self.revision = revision
        root = Path(asset_root).resolve()
        model = ET.parse(description).find(f"./world/model[@name='{robot_name}']")
        if model is None:
            raise ValueError('SELF_FILTER_ROBOT_MISSING')
        self.zero = {link.get('name'): sdf_pose(link.find('pose')) for link in model.findall('link')}
        self.joints = {}
        for joint in model.findall('joint'):
            child, parent = joint.findtext('child'), joint.findtext('parent')
            if child == 'base_link' or parent not in self.zero:
                continue  # The capture already supplies the physical base pose.
            if joint.get('type') not in {'revolute', 'prismatic'}:
                raise ValueError('SELF_FILTER_JOINT_TYPE_UNSUPPORTED')
            axis = np.fromstring(joint.findtext('axis/xyz'), sep=' ')
            if axis.shape != (3,) or not np.isfinite(axis).all() or np.linalg.norm(axis) < .99:
                raise ValueError('SELF_FILTER_JOINT_AXIS_INVALID')
            self.joints[child] = (parent, joint.get('name'), sdf_pose(joint.find('pose')), axis/np.linalg.norm(axis), joint.get('type'))
        self.meshes = {}
        for link in model.findall('link'):
            pieces = []
            for visual in link.findall('visual'):
                geometry = visual.find('geometry')
                if geometry.find('mesh') is not None:
                    uri = geometry.findtext('mesh/uri')
                    path = (root / Path(uri).relative_to('/assets/'+root.name)).resolve()
                    if not path.is_relative_to(root):
                        raise ValueError('SELF_FILTER_ASSET_OUTSIDE_ROOT')
                    mesh = trimesh.load_mesh(path, process=False)
                elif geometry.find('box') is not None:
                    mesh = trimesh.creation.box(extents=np.fromstring(geometry.findtext('box/size'), sep=' '))
                else:
                    raise ValueError('SELF_FILTER_GEOMETRY_UNSUPPORTED')
                mesh.apply_transform(sdf_pose(visual.find('pose')))
                pieces.append(mesh)
            if pieces:
                self.meshes[link.get('name')] = trimesh.util.concatenate(pieces)
        self._cache = None

    def filter(self, frame, joints, base, *, joint_stamp_ns, region=None):
        from .rgbd import deproject
        if joint_stamp_ns != frame.sequence or joint_stamp_ns <= 0:
            raise ValueError('SELF_FILTER_ENCODERS_UNSYNCHRONIZED')
        key = (frame.source_id, frame.sequence, region)
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        poses = {'base_link': base}
        def pose(name):
            if name not in poses:
                parent, motor, mount, axis, kind = self.joints[name]
                value = joints.get(motor)
                if type(value) not in (int, float) or not np.isfinite(value):
                    raise ValueError('SELF_FILTER_ENCODER_MISSING: '+motor)
                rotation = np.eye(4)
                if kind == 'revolute':
                    rotation[:3, :3] = Rotation.from_rotvec(axis*value).as_matrix()
                else:
                    rotation[:3, 3] = axis*value
                poses[name] = pose(parent) @ np.linalg.inv(self.zero[parent]) @ self.zero[name] @ mount @ rotation @ np.linalg.inv(mount)
            return poses[name]
        points, valid = deproject(frame)
        if region is not None:
            for axis, (low, high) in enumerate(region):
                valid &= (points[:, :, axis] > low) & (points[:, :, axis] < high)
        flat = points.reshape(-1, 3)
        mask = np.zeros(len(flat), dtype=bool)
        indices = np.flatnonzero(valid.ravel())
        for name, mesh in self.meshes.items():
            transform = pose(name)
            local = (flat[indices]-transform[:3, 3]) @ transform[:3, :3]
            low, high = mesh.bounds
            selected = np.flatnonzero(np.all((local >= low-.004) & (local <= high+.004), axis=1))
            if not len(selected):
                continue
            # AABB only reduces work; surface distances decide every mask bit.
            distance = mesh.nearest.on_surface(local[selected])[1]
            mask[indices[selected[distance <= .004]]] = True
        mask = mask.reshape(valid.shape)
        self._cache = (key, mask)
        return mask
