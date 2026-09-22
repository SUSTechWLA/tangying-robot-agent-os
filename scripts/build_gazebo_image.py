"""Prepare the Gazebo image before the bounded service-startup phase.

Cold ROS/Gazebo downloads can take longer than the service readiness deadline.
This build-only entry point has no simulation, actuator or credential effects.
"""
from __future__ import annotations

import os

from gazebo_process import ProcessOwner, ensure_image

if __name__ == '__main__':
    ensure_image(ProcessOwner(), os.environ.get('SIM_STACK_GAZEBO_IMAGE', 'tangying-navigation:dev'))
