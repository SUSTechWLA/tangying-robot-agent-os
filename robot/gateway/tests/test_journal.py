from tangying_robot_gateway.journal import RuntimeJournal


def test_estop_latch_survives_reopen(tmp_path):
    path = tmp_path / "runtime-journal.json"
    journal = RuntimeJournal(path)
    journal.set_estop(True, "REMOTE_EMERGENCY_STOP")

    reopened = RuntimeJournal(path)

    assert reopened.estop_latched is True
    assert reopened.estop_reason == "REMOTE_EMERGENCY_STOP"


def test_terminal_command_replays_but_conflict_is_rejected(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime-journal.json", max_commands=2)
    journal.record("key-1", "fingerprint-a", ["event-a"])

    assert journal.lookup("key-1", "fingerprint-a").status == "replay"
    assert journal.lookup("key-1", "fingerprint-a").events == ["event-a"]
    assert journal.lookup("key-1", "fingerprint-b").status == "conflict"
    assert journal.lookup("missing", "fingerprint-c").status == "missing"


def test_command_history_is_bounded(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime-journal.json", max_commands=2)
    journal.record("key-1", "one", ["one"])
    journal.record("key-2", "two", ["two"])
    journal.record("key-3", "three", ["three"])

    assert journal.lookup("key-1", "one").status == "missing"
    assert journal.lookup("key-3", "three").status == "replay"
