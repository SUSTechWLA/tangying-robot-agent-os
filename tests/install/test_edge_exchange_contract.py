"""Cloud-side interoperability check independent of the Go implementation."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_agent_data", ROOT / "scripts" / "verify_agent_data.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_agent_data_golden_checksum():
    raw = (ROOT / "core/exchangecontract/testdata/agent-data-v1.json").read_text()
    assert MODULE.verify(raw) == (
        "sha256:3db3610f17ca0b7e170d231860ae97ab5a4893d3367188b9971a37073dbbbf50"
    )
    assert '"9007199254740993"' in raw


def test_duplicate_fields_are_not_canonicalized():
    with pytest.raises(ValueError, match="duplicate"):
        MODULE.verify('{"messageId":"a","messageId":"b"}')


def test_number_spellings_and_html_escaping():
    assert MODULE.canonical({"a": MODULE.Number("1e100"), "b": "机器人<>&"}) == (
        '{"a":1e100,"b":"机器人\\u003c\\u003e\\u0026"}'
    )
