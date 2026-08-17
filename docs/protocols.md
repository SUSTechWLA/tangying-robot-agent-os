# Protocol invariants

Every physical `SkillCommand` carries:

- `schema_version`
- globally unique `command_id`
- `task_id`
- allowlisted `skill`
- absolute deadline
- short command lease
- idempotency key
- safety profile
- approval ID

The robot edge rejects an unknown schema or skill, expired command, zero lease, reused idempotency key with different content, disallowed safety profile, or missing approval.

Skill events are ordered per command and end in exactly one terminal type: succeeded, failed, cancelled, or safety-stopped. Duplicate delivery returns the stored terminal result without repeating motion.
