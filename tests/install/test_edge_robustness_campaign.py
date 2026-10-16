import json

import pytest

from scripts import run_edge_robustness_campaign as campaign


def test_go_output_fragments_are_joined_by_test_without_losing_large_records():
    rows = [{"scenario": "long", "recordType": "task", "proof": "x" * 5000},
            {"scenario": "other", "outcome": "UNKNOWN_OUTCOME"}]
    first = "    campaign_test.go:1: ROBOT_EXPERIMENT " + json.dumps(rows[0]) + "\n"
    second = "    campaign_test.go:2: ROBOT_EXPERIMENT " + json.dumps(rows[1]) + "\n"
    events = [{"Action": "output", "Package": "p", "Test": "a", "Output": first[:2000]},
              {"Action": "output", "Package": "p", "Test": "b", "Output": second},
              {"Action": "output", "Package": "p", "Test": "a", "Output": first[2000:]}]
    extracted = campaign.extract_records(events)
    assert extracted[0] == {**rows[1], "sourcePackage": "p", "sourceTest": "b"}
    assert extracted[1] == {**rows[0], "sourcePackage": "p", "sourceTest": "a"}


def test_truncated_experiment_is_rejected_instead_of_counted_as_success():
    with pytest.raises(ValueError, match="truncated"):
        campaign.extract_records([{"Action": "output", "Output": 'ROBOT_EXPERIMENT {"scenario":'}])


def test_existing_evidence_is_never_overwritten(tmp_path):
    sentinel = tmp_path / "original.json"
    sentinel.write_text('{"failed":true}')
    with pytest.raises(FileExistsError):
        campaign.run(tmp_path)
    assert sentinel.read_text() == '{"failed":true}'
