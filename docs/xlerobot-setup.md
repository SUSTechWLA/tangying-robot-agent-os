# XLeRobot setup contract

The v0.1 adapter targets the two-wheel XLeRobot implementation at upstream commit `3d14695e40c9c68229c0aacffca6053c75cd3eb6` and the matching LeRobot installation expected by that commit.

The upstream source remains external. Install XLeRobot under `/opt/XLeRobot`, follow its documented LeRobot integration steps, and make these modules importable:

```text
lerobot.robots.xlerobot_2wheels.config_xlerobot_2wheels
lerobot.robots.xlerobot_2wheels.xlerobot_2wheels
```

The adapter uses the upstream `XLerobot2WheelsConfig`, `XLerobot2Wheels.get_observation()`, `send_action()`, `stop_base()`, and `disconnect()` APIs. It never patches upstream files.

Before manipulation, all of these gates must pass:

1. Both serial ports exist and have stable udev names.
2. LeRobot can import the XLeRobot modules.
3. Calibration exists and the upstream robot reports `is_calibrated`.
4. The physical emergency stop has been tested.
5. The tabletop profile keeps `x.vel` and `theta.vel` at zero.
6. A policy supplies a bounded `action_chunk`; a high-level `pick` request without actions fails with `POLICY_ACTION_CHUNK_REQUIRED`.

Hardware availability is reported explicitly. Missing ports, integration, or calibration never produces a simulated success.
