"""测试评估器接线；模拟 HTTP 响应仅用于单元测试，不作为实验结果。"""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from grounded_llm import LLMBaseline


def test_llm_missing_configuration_is_not_a_fake_baseline(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    with pytest.raises(ValueError, match="AGENT_BASE_URL"):
        LLMBaseline(tmp_path, enabled=True)
    assert (
        LLMBaseline(tmp_path).evaluate(
            {
                "kind": "manipulation.pick",
                "common": {"params": {}},
                "tool_return_status": "SUCCESS",
                "seed": 1,
                "samples": [],
            },
            "B1",
        )
        is None
    )


def test_llm_uses_recorded_response_usage_and_replays_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BASE_URL", "http://unit-test.invalid/v1")
    monkeypatch.setenv("AGENT_MODEL", "unit-test-model")
    monkeypatch.setenv("AGENT_API_KEY", "unit-test-secret")
    response = {
        "choices": [
            {"message": {"content": '{"verdict":"UNKNOWN","failure_type":"EVIDENCE_INSUFFICIENT"}'}}
        ],
        "usage": {"total_tokens": 19},
        "model": "unit-test-model",
    }
    calls = []

    def fake_http(request, timeout):
        calls.append(request)
        assert timeout == 60
        assert "unit-test-secret" not in request.data.decode()
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_http)
    trial = {
        "kind": "manipulation.pick",
        "common": {"params": {"object": "cup"}},
        "tool_return_status": "SUCCESS",
        "seed": 1,
        "samples": [],
    }
    result = LLMBaseline(tmp_path, enabled=True).evaluate(trial, "B1")
    assert result["usage"]["total_tokens"] == 19
    monkeypatch.delenv("AGENT_BASE_URL")
    monkeypatch.delenv("AGENT_MODEL")
    assert LLMBaseline(tmp_path).evaluate(trial, "B1") == result
    assert len(calls) == 1
    assert not any("unit-test-secret" in p.read_text() for p in tmp_path.rglob("*.json"))


def test_private_model_file_is_data_and_never_persisted(tmp_path, monkeypatch):
    for name in ["AGENT_BASE_URL", "AGENT_MODEL", "AGENT_API_KEY", "TANGYING_GVF_LLM_CONFIG"]:
        monkeypatch.delenv(name, raising=False)
    private = tmp_path / "private.env"
    private.write_text(
        'AGENT_BASE_URL=https://example.invalid/v1\nAGENT_MODEL="test"\nAGENT_API_KEY=secret-test-value\nUNRELATED=$(not-a-shell)\n'
    )
    output = tmp_path / "results"
    output.mkdir()
    client = LLMBaseline(output, enabled=True, config_path=private)
    assert client.key == "secret-test-value"
    assert client.model == "test"
    assert "secret-test-value" not in (output / "llm-config.json").read_text()
    assert "UNRELATED" not in (output / "llm-config.json").read_text()


def test_malformed_model_response_abstains_with_recorded_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BASE_URL", "http://unit-test.invalid/v1")
    monkeypatch.setenv("AGENT_MODEL", "unit-test-model")
    raw = {"choices": [{"message": {"content": "not json"}}], "usage": {"total_tokens": 29}}
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: io.BytesIO(json.dumps(raw).encode())
    )
    result = LLMBaseline(tmp_path, enabled=True).evaluate(
        {
            "kind": "manipulation.pick",
            "common": {"params": {}},
            "tool_return_status": "SUCCESS",
            "seed": 1,
            "samples": [],
        },
        "B1",
    )
    assert result["verdict"] == "UNKNOWN" and result["protocol_error"]
    assert result["usage"]["total_tokens"] == 29
    assert result["response"] == raw


def test_partial_seed_cannot_be_published_as_a_complete_experiment(tmp_path):
    from grounded_experiment import merge_children

    child = tmp_path / "seeds" / "1"
    child.mkdir(parents=True)
    (child / "config.json").write_text(
        json.dumps(
            {
                "seeds": [1],
                "tasks": [{"task_id": "two-steps", "steps": ["pick", "place"]}],
                "source_sha256": {},
            }
        )
    )
    (child / "trials.jsonl").write_text(
        json.dumps({"seed": 1, "task_id": "two-steps", "step": 0}) + "\n"
    )
    with pytest.raises(ValueError, match="不完整"):
        merge_children(tmp_path, [child], 1)
    assert not (tmp_path / "trials.jsonl").exists()
