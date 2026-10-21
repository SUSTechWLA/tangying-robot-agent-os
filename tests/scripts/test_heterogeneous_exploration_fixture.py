from __future__ import annotations

import copy
import json

import pytest

from scripts import run_heterogeneous_exploration_fixture as fixture


def config(rounds=2):
    value = json.loads((fixture.ROOT / 'examples/experiments/heterogeneous_trio_fixture.json').read_text())
    value.update(seeds=[3], maxRounds=rounds)
    return value


def test_trio_starts_distinct_with_full_unchanged_roi_and_unknown_initial_map():
    scene = fixture.build_scene(3)
    original = fixture.benchmark.build_extended_scene('warehouse_aisles', 3)
    assert scene.target_ids == original.target_ids
    assert scene.truth == original.truth
    assert set(scene.starts) == set(fixture.KINDS)
    assert len({tuple(p[:3]) for p in scene.starts.values()}) == 3
    assert scene.policies['uav']['mode'] == 'volume3d'
    assert scene.policies['legged']['mode'] == scene.policies['ugv']['mode'] == 'terrain25d'
    assert all(v == -1 for v in fixture.benchmark.blank_volume(scene, 'legged')['cells'])


def test_campaign_keeps_partial_results_and_verifies_captures_and_artifacts(tmp_path):
    path = tmp_path/'config.json'
    path.write_text(json.dumps(config()))
    output = tmp_path/'experiment'
    results = fixture.campaign(path, output)
    assert len(results) == 2
    for result in results:
        assert result['status'] == 'partial'
        assert result['coverageDenominator'] == 1144
        assert all(result['perRobotCommands'][robot]['succeeded'] > 0 for robot in fixture.KINDS)
        assert result['physicalExecution'] is False
        assert result['naturalLanguageInterpreted'] is False
        assert result['rosDDSTested'] is False
    assert results[1]['faultSkips'] == ['disconnect', 'driver_rejection', 'unknown_execution']
    assert fixture.verify(output)['verifiedEpisodes'] == 2
    with pytest.raises(FileExistsError):
        fixture.campaign(path, output)
    (output/'seed-3-normal'/'summary.json').write_text('{}')
    with pytest.raises(ValueError, match='hash mismatch'):
        fixture.verify(output)


def test_unknown_commands_require_positive_durable_stop_before_following_dispatch(tmp_path):
    result = fixture.run_episode(fixture.build_scene(3), config(7), True, tmp_path/'fault')
    assert result['softwareError'] is None
    assert set(result['faultApplications']) == {'disconnect', 'driver_rejection', 'unknown_execution'}
    assert len(result['stopAcknowledgements']) == 1
    assert result['pendingUnknownTasks'] == {}
    assert result['replayedMapMessages'] > 0
    assert result['perRobotCommands']['uav']['unknown'] == 1
    assert result['perRobotCommands']['legged']['rejected'] >= 1
    events = fixture.read_journal(tmp_path/'fault'/'events.jsonl')
    stop_index = next(i for i, e in enumerate(events) if e['kind'] == 'stop_acknowledged')
    assert any(e['kind'] == 'command' and e['payload']['outcome'] == 'succeeded' for e in events[stop_index+1:])


def test_lost_stop_ack_holds_global_runner_execution_fence(tmp_path, monkeypatch):
    monkeypatch.setattr(fixture.Ports, 'stop', lambda *args: False)
    result = fixture.run_episode(fixture.build_scene(3), config(7), True, tmp_path/'fault')
    assert result['status'] == 'partial'
    assert result['pendingUnknownTasks']
    assert result['stopAcknowledgements'] == []
    events = fixture.read_journal(tmp_path/'fault'/'events.jsonl')
    index = next(i for i, e in enumerate(events) if e['kind'] == 'command' and e['payload']['outcome'] == 'unknown')
    assert not any(e['kind'] == 'command' for e in events[index+1:])
    assert any(e['kind'] == 'progress' and e['payload']['report']['reason'] == 'execution_fenced' for e in events[index+1:])


def test_pre_dispatch_software_error_is_preserved_as_failed_episode(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('synthetic policy defect')
    monkeypatch.setattr(fixture.LoggedExplorer, 'propose', fail)
    result = fixture.run_episode(fixture.build_scene(3), config(), False, tmp_path/'failure')
    assert result['status'] == 'software_error'
    assert result['softwareError'] == 'ValueError: synthetic policy defect'
    assert result['commands'] == 0


@pytest.mark.parametrize('updates', [{'seeds':[3,3]}, {'seeds':[True]}, {'maxRounds':0}, {'maxTravelM':float('inf')}, {'taskText':''}])
def test_invalid_or_unbounded_campaign_is_rejected(updates):
    value = copy.deepcopy(config())
    value.update(updates)
    with pytest.raises(ValueError):
        fixture.validate_config(value)


@pytest.fixture
def sealed_campaign(tmp_path):
    path = tmp_path/'config.json'
    path.write_text(json.dumps(config(1)))
    output = tmp_path/'experiment'
    fixture.campaign(path, output)
    return output


@pytest.mark.parametrize('field,value', [('completed', 2), ('partial', 0), ('softwareErrors', 5),
                                       ('uniqueGeometryN', 7), ('physicalValidation', True)])
def test_replay_recomputes_every_top_level_summary_field(sealed_campaign, field, value):
    output = sealed_campaign
    summary = json.loads((output/'summary.json').read_text())
    summary[field] = value
    fixture.benchmark.write_json(output/'summary.json', summary)
    # An updated manifest hash cannot make a contradictory derived metric true.
    manifest = json.loads((output/'manifest.json').read_text())
    manifest['files']['summary.json'] = fixture.sha((output/'summary.json').read_bytes())
    fixture.benchmark.write_json(output/'manifest.json', manifest)
    with pytest.raises(ValueError, match='campaign summary differs'):
        fixture.verify(output)


@pytest.mark.parametrize('mode', ['extra_file', 'omit_inventory_entry', 'remove_required_file_and_entry'])
def test_replay_requires_exact_complete_file_inventory(sealed_campaign, mode):
    output = sealed_campaign
    manifest = json.loads((output/'manifest.json').read_text())
    if mode == 'extra_file':
        (output/'unrecorded.json').write_text('{}')
    else:
        del manifest['files']['seed-3-normal/events.jsonl']
        if mode == 'remove_required_file_and_entry':
            (output/'seed-3-normal/events.jsonl').unlink()
        fixture.benchmark.write_json(output/'manifest.json', manifest)
    with pytest.raises(ValueError, match='file set'):
        fixture.verify(output)


@pytest.mark.parametrize('status,expected', [('software_error', 1), ('partial', 0), ('completed', 0)])
def test_cli_returns_failure_for_software_errors_but_accepts_partial(monkeypatch, status, expected):
    monkeypatch.setattr(fixture, 'campaign', lambda *args: [{'status': status}])
    assert fixture.main(['--config', 'unused.json', '--output', 'unused']) == expected
