# XLeRobot MuJoCo model provenance

- Upstream repository: https://github.com/Vector-Wangel/XLeRobot
- Pinned commit: `3d14695e40c9c68229c0aacffca6053c75cd3eb6`
- Upstream model source: `simulation/mujoco/xlerobot.xml`
- Upstream mesh sources: all 65 files under `simulation/mujoco/assets/` in the downloaded XLeRobot repository
- Upstream license source: `LICENSE`
- License: Apache License 2.0; the upstream license text is retained in this directory

The vendored model and meshes are an unmodified snapshot of the paths above,
except for one Tangying integration addition: a `head_depth` camera placed on
the `head_tilt_link` so the simulation matches the real depth-camera-on-head
variant. The top-level `xlerobot_tabletop.xml` scene is Tangying integration
code and is not part of the upstream snapshot.

## Fidelity notice

This pinned official model is useful as an articulated simulation reference. It is
not a calibrated digital twin of later two-wheel XLeRobot revisions, and its
dimensions, dynamics, sensors, and calibration must not be treated as current
two-wheel hardware ground truth.
