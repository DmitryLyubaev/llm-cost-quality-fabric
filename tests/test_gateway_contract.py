import json
from pathlib import Path

import pytest

from lakehouse.gateway.contract import BACKENDS, CALLERS, EVENT_FIELDS, validate_event

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "gateway-event.schema.json"


def _event(**overrides):
    event = {
        "event_time": "2026-11-12T01:02:03.456Z",
        "request_id": "req-0001",
        "session": "p10-2026-11-12",
        "api": "chat",
        "operation": "completions",
        "caller": "owner",
        "answered_by": "primary",
        "status": 200,
        "refused_by_gateway": False,
        "tokens": 123,
        "latency_ms": 840,
    }
    event.update(overrides)
    return event


def test_contract_constants():
    assert [name for name, _ in EVENT_FIELDS] == [
        "event_time", "request_id", "session", "api", "operation", "caller",
        "answered_by", "status", "refused_by_gateway", "tokens", "latency_ms",
    ]
    assert CALLERS == ("owner", "deploy", "webapp", "other")
    assert BACKENDS == ("primary", "secondary")


def test_valid_event_passes():
    assert validate_event(_event()) == []
    assert validate_event(_event(answered_by="secondary", caller="other")) == []
    assert validate_event(_event(event_time="2026-11-12T11:02:03+10:00")) == []


def test_refused_call_has_null_backend_and_tokens():
    refused = _event(status=429, refused_by_gateway=True, answered_by=None, tokens=None)
    assert validate_event(refused) == []

    # refused but a backend or a token count is present
    assert validate_event(_event(status=429, refused_by_gateway=True, tokens=None)) != []
    assert validate_event(_event(status=429, refused_by_gateway=True, answered_by=None)) != []
    # answered but the backend or the token count is null
    assert validate_event(_event(answered_by=None)) != []
    assert validate_event(_event(tokens=None)) != []


def test_guid_value_rejected():
    guid = "3f2b8c1e-9a4d-4e57-8b6a-1c2d3e4f5a6b"
    assert validate_event(_event(request_id=guid)) != []
    assert validate_event(_event(session=f"p10-{guid.upper()}")) != []
    assert validate_event(_event(operation=f"op/{guid}")) != []


def test_unknown_key_rejected():
    assert validate_event(_event(client_ip="10.0.0.1")) != []
    assert validate_event(_event(model="gpt")) != []
    event = _event()
    del event["latency_ms"]
    assert validate_event(event) != []


@pytest.mark.parametrize(
    "overrides",
    [
        {"caller": "someone"},
        {"caller": None},
        {"answered_by": "tertiary"},
        {"status": "200"},
        {"status": True},
        {"status": 200.0},
        {"refused_by_gateway": 0},
        {"tokens": "12"},
        {"latency_ms": None},
        {"request_id": 7},
        {"event_time": "2026-11-12T01:02:03"},
        {"event_time": "2026-11-12 01:02:03Z"},
        {"event_time": "2026-13-45T01:02:03Z"},
        {"event_time": 1762909323},
    ],
)
def test_wrong_type_or_value_rejected(overrides):
    assert validate_event(_event(**overrides)) != []


def test_non_dict_rejected():
    assert validate_event([]) != []


def test_schema_file_matches_fields():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    props = schema["properties"]

    assert list(props) == [name for name, _ in EVENT_FIELDS]
    assert schema["required"] == [name for name, _ in EVENT_FIELDS]
    assert schema["additionalProperties"] is False

    json_type = {"string": "string", "int": "integer", "bool": "boolean", "datetime": "string"}
    nullable = {"answered_by", "tokens"}
    for name, kind in EVENT_FIELDS:
        types = props[name]["type"]
        types = types if isinstance(types, list) else [types]
        assert json_type[kind] in types, name
        assert ("null" in types) == (name in nullable), name
        assert (props[name].get("format") == "date-time") == (kind == "datetime"), name

    assert props["caller"]["enum"] == list(CALLERS)
    assert props["answered_by"]["enum"] == [*BACKENDS, None]
