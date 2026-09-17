"""Run the robot side of the pairing protocol for the cross-language test.

This is the counterpart of ``internal/pairing/crosslang_test.go``: the Go agent
pairs with this process, which is the robot's own code doing the robot's own job.
The fixture in ``tests/contract/robot_pairing.json`` proves the two sides agree on
a key and a ciphertext; this proves they can actually complete a pairing —
framing, ordering, timeouts, and the certificate written to disk.

Usage::

    python -m tests.pairing.python_robot_pair_server ROBOT_ID STATE_DIR CERT_DIR

It prints one JSON object per line: zero or more ``{"event": ...}`` records, then
exactly one ``{"ready": true, "port": ..., "code": ...}`` once the window is open,
then further events (notably ``pairing.paired``) as they happen. The test reads
until it sees the readiness line rather than assuming the first line is it,
because the window opening is itself an event.

This file lives in the repository on purpose. An earlier version of the test
pointed at ``/tmp/python_robot_pair_server.py``, so it passed only on the machine
where somebody had happened to create that file and failed everywhere else — a
test that cannot pass is worse than no test, because it reports the code as
broken when the harness is.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

# The gateway package lives beside the repository root; importing it by path keeps
# this script runnable without installing anything.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot" / "gateway"))

from tangying_robot_gateway.pairing import EnrollmentServer, PairingState  # noqa: E402


def emit(record: dict) -> None:
    """Write one line and flush it.

    Flushing matters: the reader is blocked on this pipe, so a buffered line is a
    line that has not been sent, and the test would time out waiting for something
    that already happened.
    """
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        sys.stderr.write("usage: python_robot_pair_server ROBOT_ID STATE_DIR CERT_DIR\n")
        return 2
    robot_id, state_directory, certificate_directory = argv[1], Path(argv[2]), Path(argv[3])
    state_directory.mkdir(parents=True, exist_ok=True)

    events: list[tuple[str, dict]] = []
    lock = threading.Lock()

    def on_event(event: str, detail: dict) -> None:
        with lock:
            events.append((event, detail))
        emit({"event": event, "detail": detail})

    state = PairingState(directory=state_directory)
    # Port 0 asks the operating system to choose, so two runs of this test cannot
    # collide on one fixed port. The test learns the real port from the readiness
    # line rather than assuming it.
    server = EnrollmentServer(
        state=state,
        certificate_directory=certificate_directory,
        robot_id=robot_id,
        port=0,
        on_event=on_event,
    )
    if not server.start():
        emit({"event": "pairing.failed", "detail": {"reason": "the window did not open"}})
        return 1

    emit({"ready": True, "port": server.bound_port, "code": state.code})

    # Wait for the pairing to complete, the window to expire, or the reader to go
    # away. The loop is bounded by the window itself, so this cannot hang.
    deadline = time.monotonic() + float(server.window_seconds) + 5.0
    while time.monotonic() < deadline:
        with lock:
            paired = any(name == "pairing.paired" for name, _ in events)
        if paired or not server.open:
            break
        time.sleep(0.05)

    server.stop()
    # A short grace period so the final events reach the reader before the process
    # exits and the pipe closes under it.
    time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
