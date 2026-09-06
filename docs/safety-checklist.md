# Physical hardware safety checklist

Use this with the [purchased-XLeRobot guide](sim2real/README.md) and [first-trial procedure](install/xlerobot-experiment.md). The repository has no completed physical acceptance result. A software READY result is not permission to move hardware.

Before any supervised trial, the operator must verify:

- [ ] An independent physical emergency stop removes actuator power without software, and is reachable throughout the trial.
- [ ] The workspace is clear of people, pets, cables and fragile objects; the operator stays outside the motion envelope.
- [ ] Purchased hardware, controller mapping, firmware, pinned driver and calibration identities match the recorded kit.
- [ ] Serial ports have stable names and correct permissions; no other service owns the same controllers.
- [ ] Calibration is current; enabling torque or calibration requires explicit local supervision.
- [ ] The tabletop profile rejects all `x.vel` and `theta.vel` action keys; mobile-base operation is outside this profile.
- [ ] Joint ranges, relative target, action length, speed and workspace limits are selected and validated for this device. Defaults `8.0` and `64` are software defaults, not hardware-certified limits.
- [ ] Arms are supported before starting/stopping the service: default systemd startup connects torque-off, which disables existing torque and configures registers. Connection is not physically inert.
- [ ] Explicit local arm authorization is complete for this process; a restarted service does not automatically arm.
- [ ] Empty-arm, low-speed bench motion and controlled cancellation succeed before payload work.
- [ ] Software stop and physical emergency stop have been tested with actual response times recorded.
- [ ] Network loss is tested against the configured command lease/watchdog and hardware response budget; no fixed one-second guarantee is assumed.
- [ ] Stale, duplicated or uncertain commands do not repeat physical movement; journal and observation reconciliation are verified.
- [ ] Real perception and verification agree with the observed result; the first payload is soft, light, non-liquid and non-sharp.

After an emergency stop, inspect the robot and workspace before local explicit clearance. The cloud cannot clear the latch; restarting or deleting the journal is not a recovery procedure. Record passed and failed trials, configuration identity and evidence using the Sim2Real kit.
