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


def test_resource_grant_survives_reopen_and_cannot_move_backwards(tmp_path):
    path = tmp_path / "runtime-journal.json"
    journal = RuntimeJournal(path)
    journal.set_resource_grant("block:red-block", "robot-1", 8)

    reopened = RuntimeJournal(path)

    assert reopened.resource_grants == {"block:red-block": ("robot-1", 8)}


def test_unresolved_command_is_never_evicted_by_terminal_history(tmp_path):
    path = tmp_path / "runtime-journal.json"
    journal = RuntimeJournal(path, max_commands=1)
    journal.begin("uncertain", "motion")
    journal.record("completed", "observation", ["event"])

    reopened = RuntimeJournal(path, max_commands=1)
    assert reopened.lookup("uncertain", "motion").status == "pending"
    assert reopened.estop_latched


def test_large_receipts_are_immutable_blobs_not_rewritten_in_index(tmp_path):
    import json
    path = tmp_path/'commands.json'
    journal = RuntimeJournal(path)
    original = ['01'*500_000]
    journal.record('large', 'fingerprint', original)
    index = json.loads(path.read_text())
    blob = path.with_name(path.name+'.events')/(index['commands']['large']['event_blob']+'.json')
    before = blob.stat().st_mtime_ns
    journal.begin('pending', 'motion')
    assert path.stat().st_size < 1024
    assert blob.stat().st_mtime_ns == before
    reopened = RuntimeJournal(path)
    assert reopened.lookup('large', 'fingerprint').events == original
    assert reopened.lookup('pending', 'motion').status == 'pending'
    assert reopened.estop_latched


def test_v1_journal_migrates_without_changing_replay_or_resource_fencing(tmp_path):
    import json
    path = tmp_path/'commands.json'
    path.write_text(json.dumps({'version':1,'estop_latched':False,'estop_reason':'',
        'commands':{'old':{'fingerprint':'old-f','events':['original-exact-bytes']}},
        'resource_grants':{'cup':{'owner':'robot','token':8}}}))
    journal = RuntimeJournal(path)
    assert journal.lookup('old','old-f').events == ['original-exact-bytes']
    journal.record('new','new-f',['new'])
    assert json.loads(path.read_text())['version'] == 2
    reopened = RuntimeJournal(path)
    assert reopened.lookup('old','old-f').events == ['original-exact-bytes']
    assert reopened.resource_grants == {'cup':('robot',8)}


def test_blob_corruption_or_absence_latches_stop_instead_of_reexecuting(tmp_path):
    import json
    path = tmp_path/'commands.json'
    journal = RuntimeJournal(path)
    journal.record('key','fp',['original'])
    document = json.loads(path.read_text())
    blob = path.with_name(path.name+'.events')/(document['commands']['key']['event_blob']+'.json')
    blob.write_text('["changed"]')
    reopened = RuntimeJournal(path)
    assert reopened.estop_latched
    assert reopened.estop_reason.startswith('RUNTIME_JOURNAL_INVALID')
    blob.unlink()
    assert RuntimeJournal(path).estop_latched


def test_blob_eviction_occurs_only_after_index_commit_and_retains_unknown(tmp_path, monkeypatch):
    import json

    import pytest
    path = tmp_path/'commands.json'
    journal = RuntimeJournal(path,max_commands=1)
    journal.record('old','old',['old'])
    old_index = json.loads(path.read_text())
    old_blob = path.with_name(path.name+'.events')/(old_index['commands']['old']['event_blob']+'.json')
    atomic_write = journal._atomic_write
    def fail_index(target, payload):
        if target == path:
            raise OSError('index fsync failure')
        atomic_write(target,payload)
    monkeypatch.setattr(journal,'_atomic_write',fail_index)
    with pytest.raises(OSError):
        journal.record('new','new',['new'])
    assert old_blob.exists()
    assert RuntimeJournal(path).lookup('old','old').events == ['old']
    monkeypatch.setattr(journal,'_atomic_write',atomic_write)
    journal.begin('unknown','unknown')
    journal.record('new','new',['new'])
    assert not old_blob.exists()
    assert len(list(path.with_name(path.name+'.events').glob('*.json'))) == 1
    assert RuntimeJournal(path).lookup('unknown','unknown').status == 'pending'
