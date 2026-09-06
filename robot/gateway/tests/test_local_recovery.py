from types import SimpleNamespace

import pytest
from tangying_robot_gateway.journal import RuntimeJournal
from tangying_robot_gateway.local_recovery import exclusive_runtime, reset_local


def test_runtime_lock_rejects_second_process_owner(tmp_path):
    path = tmp_path / "state.json"
    with exclusive_runtime(path), pytest.raises(RuntimeError, match="already owns"), exclusive_runtime(path):
        pass
    with exclusive_runtime(path):
        pass


def test_unknown_motion_recovery_is_audited_and_never_reexecutes_old_key(tmp_path):
    path = tmp_path / "state.json"
    original = RuntimeJournal(path)
    original.begin("unknown", "motion-fingerprint")
    journal = RuntimeJournal(path)
    assert journal.estop_latched
    backend = SimpleNamespace(reset_stop=lambda **kwargs: True)
    reset_local(backend, journal, operator_present=True, interactive=True, operator="onsite", reason="inspected and removed held object; cancelled old task")
    reopened = RuntimeJournal(path, max_commands=1)
    assert not reopened.estop_latched
    assert reopened.lookup("unknown", "motion-fingerprint").status == "reconciled"
    assert reopened.lookup("unknown", "changed").status == "conflict"
    for i in range(5):
        reopened.record(f"other-{i}", "fingerprint", ["terminal"])
    again = RuntimeJournal(path, max_commands=1)
    assert again.lookup("unknown", "motion-fingerprint").status == "reconciled"
    assert len(list(tmp_path.glob("*.recovery-*.json"))) == 1


def test_reset_refuses_noninteractive_and_corrupt_journal(tmp_path):
    path = tmp_path / "state.json"
    journal = RuntimeJournal(path)
    with pytest.raises(ValueError, match="interactive"):
        reset_local(None, journal, operator_present=True, interactive=False, operator="onsite", reason="inspection")
    assert not list(tmp_path.glob("*.recovery-*.json"))
    path.write_text("not json")
    with pytest.raises(ValueError, match="corrupt"):
        reset_local(None, RuntimeJournal(path), operator_present=True, interactive=True, operator="onsite", reason="inspection")
    assert path.read_text() == "not json"


def test_failed_backend_reset_retains_stop(tmp_path):
    path = tmp_path / "state.json"
    journal = RuntimeJournal(path)
    journal.set_estop(True, "test")
    backend = SimpleNamespace(reset_stop=lambda **kwargs: False, stop=lambda reason: None)
    with pytest.raises(RuntimeError, match="reset failed"):
        reset_local(backend, journal, operator_present=True, interactive=True, operator="onsite", reason="inspection")
    assert RuntimeJournal(path).estop_latched
