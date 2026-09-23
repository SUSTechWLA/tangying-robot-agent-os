#!/usr/bin/env python3
"""Release a persisted runtime safety latch through the audited local path.

A latched runtime refuses every physical command, and the latch is deliberately
durable -- it is written to the runtime journal and survives a restart, so a
stack that was latched will not even become ready again (the readiness probe
requires ``Ready: true``, and a latched runtime truthfully reports ``false``).
Clearing it is an attended operation by design.

The documented operator command is

    python -m tangying_robot_gateway.run_direct_edge --reset-stop \\
        --operator-present --operator NAME --reset-reason TEXT --journal PATH

but it builds an ``XLeRobotDirectBackend`` before doing anything else, and that
needs the ``xlerobot_adapter`` ROS 2 package, which is absent outside the robot
image. The reset itself lives in ``local_recovery.reset_local``, and every guard
that matters is inside it: the interactive-terminal requirement, the audit file
written *before* any state changes, the refusal to touch a corrupt journal, the
exclusive journal lock, and the reconciliation of unresolved commands. This tool
calls that same function with the same arguments, supplying a stand-in backend
only because the real one cannot be constructed off-robot.

The stand-in is sound here because ``reset_local`` asks for a backend reset via
``getattr(backend, "reset_stop", None)`` and skips a backend that lacks it rather
than treating that as a failure -- which is the path a Gazebo runtime takes too,
since ``GazeboSkillBackend`` has no ``reset_stop``. Nothing here connects, arms,
or replays a command.

Usage:
    python scripts/reset_runtime_latch.py --journal PATH --operator NAME --reason TEXT
"""

import argparse
import pathlib
import sys

# Resolved from this file rather than hardcoded, so the tool works from any
# checkout. scripts/ sits directly under the root this package is imported from.
REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "robot" / "gateway"))

from tangying_robot_gateway.journal import RuntimeJournal
from tangying_robot_gateway.local_recovery import exclusive_runtime, reset_local


class UnarmedBackend:
    """A backend that owns no hardware: the reset path never needs one."""

    def capabilities(self):
        return None

    def stop(self, reason):
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", required=True, type=pathlib.Path)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()

    if not args.journal.exists():
        print(f"no journal at {args.journal}; nothing to reset", file=sys.stderr)
        return 2

    before = RuntimeJournal(args.journal)
    print(f"before: latched={before.estop_latched} reason={before.estop_reason!r}")

    # The CLI passes sys.stdin.isatty() here; under `script` that is True, which
    # is the same attended condition the operator command enforces.
    interactive = sys.stdin.isatty()
    if not interactive:
        print("refusing: reset requires an interactive terminal", file=sys.stderr)
        return 3

    with exclusive_runtime(args.journal):
        journal = RuntimeJournal(args.journal)
        reset_local(UnarmedBackend(), journal,
                    operator_present=True, interactive=True,
                    operator=args.operator, reason=args.reason)

    after = RuntimeJournal(args.journal)
    print(f"after:  latched={after.estop_latched} reason={after.estop_reason!r}")
    return 0 if not after.estop_latched else 1


if __name__ == "__main__":
    raise SystemExit(main())
