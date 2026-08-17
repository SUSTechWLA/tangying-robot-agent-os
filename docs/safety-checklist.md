# Physical hardware safety checklist

Do not run payload manipulation until every item is checked by the operator:

- [ ] A physical emergency stop removes actuator power without software.
- [ ] The operator can reach the emergency stop throughout the trial.
- [ ] The workspace is clear of people, pets, cables, and breakable objects.
- [ ] Both serial ports use stable udev names and correct permissions.
- [ ] XLeRobot calibration is current and loaded without an interactive recalibration prompt.
- [ ] The tabletop profile rejects non-zero `x.vel` and `theta.vel`.
- [ ] Joint range and maximum relative target limits are configured.
- [ ] Empty-arm home and low-speed motion succeed.
- [ ] Software stop and physical emergency stop have been tested.
- [ ] Disconnecting the laptop causes a local stop within one second.
- [ ] A stale or duplicate command does not repeat movement.
- [ ] The first payload is soft, light, non-liquid, non-sharp, and non-fragile.

After an emergency stop, inspect the workspace and robot before local manual clearance. The cloud cannot clear the stop latch.
