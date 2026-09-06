"""Local, attended recovery with process exclusion. No network reset endpoint."""
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .safety import SafetySupervisor


@contextmanager
def exclusive_runtime(journal_path: Path):
    journal_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(str(journal_path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("runtime already owns this journal; stop the service before local recovery or foreground start") from exc
        yield
    finally:
        os.close(descriptor)


def reset_local(backend, journal, *, operator_present: bool, interactive: bool, operator: str, reason: str) -> None:
    if not operator_present or not interactive or not operator.strip() or not reason.strip():
        raise ValueError("local reset requires --operator-present, interactive terminal, --operator and --reset-reason")
    if journal.estop_reason.startswith("RUNTIME_JOURNAL_INVALID"):
        raise ValueError("corrupt runtime journal requires engineering recovery from a reviewed backup; reset refused")
    if journal.path is None:
        raise ValueError("local reset requires a durable journal")
    # Preserve the original stop/uncertain command records with the operator's
    # explanation before modifying safety state. Failure to save prevents reset.
    audit = journal.path.with_name(f"{journal.path.stem}.recovery-{uuid4()}.json")
    original = json.loads(journal.path.read_text()) if journal.path.exists() else {}
    descriptor = os.open(audit, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump({"operator": operator, "reason": reason, "originalJournal": original}, output)
        output.flush()
        os.fsync(output.fileno())
    journal.reconcile_pending(operator=operator, reason=reason)
    safety = SafetySupervisor(backend=backend, journal=journal)
    if not safety.clear_local(operator_present=True):
        raise RuntimeError(f"local reset failed: {safety.last_stop_reason}")
