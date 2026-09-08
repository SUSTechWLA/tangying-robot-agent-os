"""Deployment-time reference fixture calibration, never task-time placement.

These dimensions describe the commissioned physical fixtures. Perception uses
the dimensions with measured RGB-D edges, never their configured world poses.
Legacy scene assets are unchanged. New installations must commission their own
supported workcell and validate its complete navigation/manipulation loop.
"""

import mujoco
import numpy as np

WORKCELL_REVISION = "supported-navigation-workcell-v3"
BIN_DIMENSIONS_M = (.15, .10)
TRAY_DIMENSIONS_M = (.36, .13)
MOBILE_START_Y_M = -.60
APPROACH_GOAL_Y_M = .05
NAVIGATION_WORLD_Y_MIN_M = -.65


def commission_model(spec):
    # Physically rendered, seeded floor markings make the base camera's view
    # observable after self-filtering the stowed robot. The detector and SLAM
    # receive only normal RGB-D pixels; no feature/pose/occupancy is injected.
    # The 4 m material patch avoids periodic checkerboard ambiguity on this route.
    rng = np.random.default_rng(20260909)
    patches = rng.choice([55, 95, 140, 190, 225], size=(128, 128))
    grey = np.repeat(np.repeat(patches, 4, axis=0), 4, axis=1)
    pixels = np.repeat(grey[:, :, None], 3, axis=2).astype(np.uint8)
    spec.add_texture(name="navigation_floor_texture", type=mujoco.mjtTexture.mjTEXTURE_2D,
                     width=512, height=512, nchannel=3, data=pixels.reshape(-1))
    material = spec.add_material(name="navigation_floor_material", texuniform=True,
                                 texrepeat=[.25, .25], rgba=[1, 1, 1, 1], specular=0)
    material.textures[int(mujoco.mjtTextureRole.mjTEXROLE_RGB)] = "navigation_floor_texture"
    spec.geom("floor").material = material.name
    spec.geom("floor").rgba = [1, 1, 1, 1]
    # The old front edge (.26 m) overlapped the stowed navigation footprint at
    # its .05 m goal. Shorten the tabletop in the *model* and relocate its real
    # support legs. No obstacle pixels or collision shapes are filtered away.
    spec.geom("tabletop").size[1] = .30  # world Y [.35, .95]
    for side in ("left", "right"):
        spec.geom(f"table_leg_front_{side}").pos[1] = -.265
        spec.geom(f"table_leg_back_{side}").pos[1] = .265
    for name in ("left_bin", "right_bin"):
        body = spec.body(name)
        body.pos[1:] = [.40, .75]
        for geom in body.geoms:
            geom.size[:2] = [value / 2 for value in BIN_DIMENSIONS_M]
    # Every container bottom now rests on the tabletop at Z=.73. Sources remain
    # at Y=.49 outside the side bins; execution never relocates source objects.
    spec.body("front_tray").pos[1:] = [.415, .75]
