# Sensor admission deadline boundary

The expanded gateway regression after the run-006 grasp repair exposed
`test_sensor_wait_never_overrides_command_deadline`: a 200 ms command could end
with `CAPABILITY_UNAVAILABLE` instead of `COMMAND_EXPIRED`. No actuator dispatch
was involved in this failure.

`SafetySupervisor.start` checks the deadline before inspecting capabilities. A
read-only capability inspection can cross that deadline and still return its
earlier sensor rejection. The admission loop then observes that its monotonic
budget has ended and returns the stale decision. Separately, sampling monotonic
time before wall time can shorten the converted duration if execution is
preempted between those reads.

The service now derives the remaining deadline from the supervisor's own clock
before anchoring the monotonic limit. Each sleep is bounded by both the five
second sensor limit and the current command deadline. If an unavailable result
has crossed that deadline, one final call to the existing supervisor resolves
the result without another sleep. Its emergency-stop and active-command priority
remain authoritative. Approval, profile, sensor freshness, lease, journal and
execution gates are unchanged; the original 200 ms test deadline is unchanged.

Deterministic before/after replay shows:

| Case | Previous result | Updated result | Dispatches |
| --- | --- | --- | --- |
| Capability read crosses deadline | unavailable at 200 ms | expired at 200 ms | 0 |
| Two ms clock-sampling preemption | unavailable at 198 ms | expired at 200 ms | 0 |

Additional tests preserve emergency-stop and busy precedence, prevent a late
connection check from adding another polling interval, and retain a five second
sensor-budget result when the command's own deadline is still in the future.
These are offline regression results, not a claim of a completed physical task.

Evidence retained under
`artifacts/acceptance/long-horizon-20261010/grasp-diagnosis-006/`:

- `sensor-admission-deadline-reproduction.json`: historical method versus patched
  method using the same deterministic clock and real supervisor.
- `sensor-admission-deadline-regression.log`: gateway, plugin, safety, journal and
  original grasp-source replay tests.
- `sensor-admission-deadline-freeze.json`: source and validation hashes.

The runtime must receive the updated `service.py` and restart through the normal
maintenance process before a new focused physical task. Existing run-006 records
and its unknown physical parent outcome remain unchanged.
