#!/usr/bin/env python3
"""Fetch pinned furnishing sources. The deployed house is the XLeRobot home."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://github.com/aws-robotics/aws-robomaker-small-house-world.git'
REVISION = 'ff9631ca6d1db9c1ba656498151464b5ab74aafe'


def prepare(root, spawn=None):
    root.mkdir(parents=True,exist_ok=True)
    checkout=root/'aws-small-house'
    if not checkout.exists():
        subprocess.run(['git','clone','--filter=blob:none','--no-checkout',URL,str(checkout)],check=True)
        subprocess.run(['git','-C',str(checkout),'checkout',REVISION],check=True)
    revision=subprocess.check_output(['git','-C',str(checkout),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.check_output(['git','-C',str(checkout),'status','--porcelain'],text=True).strip()
    if revision!=REVISION or dirty:
        raise ValueError('asset checkout differs from the pinned clean revision; use a new --output directory')
    shutil.copyfile(checkout/'LICENSE',root/'ASSET-LICENSE')
    return {'repository':URL,'revision':revision,'source':str(checkout.resolve()),
            'assetOnly':True,'next':'prepare_furnished_home.py, then export_gazebo_home.py'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/sim-assets')
    args=parser.parse_args();print(json.dumps(prepare(args.output),indent=2))
