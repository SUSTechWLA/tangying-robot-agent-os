"""Deployment-time reference fixture calibration, never task-time placement.

These dimensions describe the commissioned physical fixtures. Perception uses
the dimensions with measured RGB-D edges, never their configured world poses.
Legacy scene assets are unchanged. New installations must commission their own
supported workcell and validate its complete navigation/manipulation loop.
"""

WORKCELL_REVISION = "supported-navigation-workcell-v2"
BIN_DIMENSIONS_M = (.15, .10)
TRAY_DIMENSIONS_M = (.36, .13)


def commission_model(spec):
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
