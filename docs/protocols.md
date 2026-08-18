# Protocol invariants

Every physical `SkillCommand` carries:

- `schema_version`
- globally unique `command_id`
- `task_id`
- allowlisted `skill`
- absolute deadline
- short command lease (bounded by the runtime)
- idempotency key
- safety profile
- approval ID

The robot edge rejects an unknown schema or skill, missing task/command
identity, expired command, missing or over-long lease, reused idempotency key
with different content, disallowed safety profile, missing approval, and
action chunks that contain mobile-base keys, unknown action keys, non-finite
values or values outside the tabletop bounds.

Skill events are ordered per command and end in exactly one terminal type:
succeeded, failed, cancelled, or safety-stopped. Duplicate delivery returns
the stored terminal result without repeating motion. `Cancel` is a controlled
single-command stop; only local operator clearance can release an E-stop latch.

`RobotCapabilities.capabilities` is the Agent-facing capability registry.
`Observation.semantic_state` is the low-rate semantic state channel; raw
sensor/joint streams are not part of the Agent contract.

Compound one-sentence requests are persisted as an ordered
`manipulation.Intent.sequence`. The Local Agent renews its cloud task lease
every 20 seconds while executing the sequence and cancels local execution if
renewal fails, so a second agent cannot claim and repeat physical steps.
