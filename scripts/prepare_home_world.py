#!/usr/bin/env python3
"""Fetch pinned static house assets and compose them with our Harmonic robot."""
import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://github.com/aws-robotics/aws-robomaker-small-house-world.git'
REVISION = 'ff9631ca6d1db9c1ba656498151464b5ab74aafe'


def prepare(root, spawn):
    root.mkdir(parents=True, exist_ok=True)
    checkout = root/'aws-small-house'
    if not checkout.exists():
        subprocess.run(['git','clone','--filter=blob:none','--no-checkout',URL,str(checkout)],check=True)
        subprocess.run(['git','-C',str(checkout),'checkout',REVISION],check=True)
    revision = subprocess.check_output(['git','-C',str(checkout),'rev-parse','HEAD'],text=True).strip()
    dirty = subprocess.check_output(['git','-C',str(checkout),'status','--porcelain'],text=True).strip()
    if revision != REVISION or dirty:
        raise ValueError('asset checkout differs from the pinned clean revision; use a new --output directory')
    adapted = root/'harmonic-models'
    shutil.copytree(checkout/'models', adapted, dirs_exist_ok=True)
    shutil.copyfile(checkout/'LICENSE', root/'ASSET-LICENSE')
    shutil.copytree(checkout/'photos', root/'photos', dirs_exist_ok=True)
    for mesh in adapted.glob('*/meshes/*'):
        if mesh.suffix.lower() == '.dae':
            text = mesh.read_text()
            mesh.write_text(text.replace('../../../../photos/', '../../../photos/'))
    for model_file in adapted.glob('*/model.sdf'):
        model_tree = ET.parse(model_file)
        for parent in model_tree.getroot().iter():
            for child in list(parent):
                if child.tag in {'inertial', 'plugin'}:
                    parent.remove(child)
            if parent.tag == 'pose' and parent.get('frame') == '':
                parent.attrib.pop('frame')
        model_tree.write(model_file,encoding='utf-8',xml_declaration=True)
    tree = ET.parse(ROOT/'robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf')
    world = tree.getroot().find('world')
    for model in list(world.findall('model')):
        if model.get('name') != 'tangying_robot':
            world.remove(model)
        else:
            model.find('pose').text = ' '.join(str(v) for v in [*spawn,.16,0,0,0])
    upstream = ET.parse(checkout/'worlds/small_house.world').getroot().find('world')
    count = 0
    for model in upstream.findall('model'):
        instance = copy.deepcopy(model)
        # Imported household meshes are static environment assets. We run our
        # maintained Harmonic systems, never upstream Classic plugins.
        static = instance.find('static')
        if static is None: static = ET.SubElement(instance,'static')
        static.text = 'true'
        for parent in instance.iter():
            for plugin in list(parent.findall('plugin')): parent.remove(plugin)
        for pose in instance.iter('pose'):
            if pose.get('frame') == '': pose.attrib.pop('frame')
        world.append(instance)
        count += 1
    for uri in world.findall('.//uri'):
        value = uri.text or ''
        if value.startswith('model://') and not (checkout/'models'/value[8:]/'model.sdf').is_file():
            raise ValueError(f'unresolved model: {value}')
    output = root/'aws-small-house-harmonic.sdf'
    ET.indent(tree, space='  ')
    tree.write(output,encoding='utf-8',xml_declaration=True)
    provenance = {'repository': URL,'revision': revision,'license': 'LICENSE in upstream checkout',
        'modelCount': count,'worldSHA256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'assetOnly': True,'spawn': spawn,'requiresSurvey': True,'requiresCommissioning': True}
    (root/'home-world-source.json').write_text(json.dumps(provenance,indent=2)+'\n')
    return dict(world=str(output.resolve()),models=str(adapted.resolve()),**provenance)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/sim-assets')
    parser.add_argument('--spawn',nargs=2,type=float,default=[0.,0.],metavar=('X','Y'))
    args = parser.parse_args()
    print(json.dumps(prepare(args.output,args.spawn),ensure_ascii=False,indent=2))
