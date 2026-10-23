"""Tests for the development-only gateway event replay (tools/replay_gateway_events.py).

No test imports azure-eventhub: `send` takes an injected sender, and the CLI is driven through the
module's `_make_sender` seam.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

import replay_gateway_events as replay
from lakehouse.gateway.contract import BACKENDS, CALLERS, validate_event

START = datetime(2026, 10, 24, 1, 0, 0, tzinfo=timezone.utc)
TIME_FORMAT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")


def test_every_event_is_valid():
    for event in replay.make_events(200, START, seed=1):
        assert validate_event(event) == [], event


def test_every_event_is_session_replay():
    assert {e["session"] for e in replay.make_events(100, START, seed=1)} == {"replay"}


def test_events_are_deterministic_for_a_seed_and_differ_across_seeds():
    first = replay.make_events(60, START, seed=7)
    assert first == replay.make_events(60, START, seed=7)
    assert first != replay.make_events(60, START, seed=8)


def test_the_global_random_state_is_not_used():
    import random

    random.seed(123)
    expected = random.random()
    random.seed(123)
    replay.make_events(20, START, seed=1)
    assert random.random() == expected


def test_request_ids_are_unique_guids():
    ids = [e["request_id"] for e in replay.make_events(100, START, seed=3)]
    assert len(set(ids)) == 100
    assert all(re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", i) for i in ids)


def test_the_mix_covers_every_caller_both_backends_and_a_refusal():
    events = replay.make_events(50, START, seed=1)
    assert {e["caller"] for e in events} == set(CALLERS)
    assert {e["answered_by"] for e in events if e["answered_by"]} == set(BACKENDS)
    refusals = [e for e in events if e["refused_by_gateway"]]
    assert refusals
    for event in refusals:
        assert event["status"] in (401, 403, 429)
        assert event["answered_by"] is None
        assert event["tokens"] is None


def test_event_time_increases_and_ends_in_z():
    times = [e["event_time"] for e in replay.make_events(100, START, seed=1)]
    assert all(TIME_FORMAT.fullmatch(t) for t in times)
    assert times == sorted(set(times))  # strictly increasing
    assert times[0] >= "2026-10-24T01:00:00.000Z"


def test_a_non_utc_start_is_written_in_utc():
    start = datetime(2026, 10, 24, 11, 0, 0, tzinfo=timezone(timedelta(hours=10)))
    assert replay.make_events(1, start, seed=1)[0]["event_time"].startswith("2026-10-24T01:00:")


def test_a_naive_start_raises():
    with pytest.raises(ValueError):
        replay.make_events(5, datetime(2026, 10, 24, 1, 0, 0), seed=1)


class FakeBatch:
    def __init__(self, capacity):
        self.capacity = capacity
        self.items = []

    def add(self, item):
        if len(self.items) >= self.capacity:
            raise ValueError("batch is full")
        self.items.append(item)


class FakeSender:
    def __init__(self, capacity=1000):
        self.capacity = capacity
        self.sent = []
        self.closed = False

    def create_batch(self):
        return FakeBatch(self.capacity)

    def send_batch(self, batch):
        self.sent.append(batch.items)

    def close(self):
        self.closed = True


def test_send_returns_the_count_and_closes_the_sender():
    events = replay.make_events(10, START, seed=1)
    sender = FakeSender()
    assert replay.send(events, "ns", "hub", sender=sender) == 10
    assert sender.closed
    assert len(sender.sent) == 1
    assert [json.loads(item) for item in sender.sent[0]] == events


def test_send_starts_a_new_batch_when_one_is_full():
    events = replay.make_events(10, START, seed=1)
    sender = FakeSender(capacity=4)
    assert replay.send(events, "ns", "hub", sender=sender) == 10
    assert [len(batch) for batch in sender.sent] == [4, 4, 2]
    assert [json.loads(i) for batch in sender.sent for i in batch] == events
    assert sender.closed


def test_send_closes_the_sender_when_sending_fails():
    class Failing(FakeSender):
        def send_batch(self, batch):
            raise RuntimeError("boom")

    sender = Failing()
    with pytest.raises(RuntimeError):
        replay.send(replay.make_events(3, START, seed=1), "ns", "hub", sender=sender)
    assert sender.closed


def test_send_with_no_events_sends_nothing():
    sender = FakeSender()
    assert replay.send([], "ns", "hub", sender=sender) == 0
    assert sender.sent == []
    assert sender.closed


def test_importing_the_module_does_not_import_azure_eventhub():
    assert "azure.eventhub" not in sys.modules


def test_the_default_host_is_built_from_the_namespace():
    host = replay._host("ns")
    assert host.startswith("ns.servicebus") and host.endswith(".windows.net")


def _cli_env(monkeypatch, sender):
    monkeypatch.setenv("P10_EVENTHUB_NAMESPACE", "secret-namespace")
    monkeypatch.setenv("P10_EVENTHUB_NAME", "secret-hub")
    seen = []
    monkeypatch.setattr(replay, "_make_sender", lambda namespace, hub: seen.append((namespace, hub)) or sender)
    return seen


def test_the_cli_prints_only_the_count(monkeypatch, capsys):
    sender = FakeSender()
    seen = _cli_env(monkeypatch, sender)
    assert replay.main(["--count", "12", "--seed", "5"]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == "sent 12 replay events"
    assert err == ""
    assert seen == [("secret-namespace", "secret-hub")]
    assert sum(len(batch) for batch in sender.sent) == 12
    assert sender.closed


def test_the_cli_defaults_to_50_events(monkeypatch, capsys):
    _cli_env(monkeypatch, FakeSender())
    assert replay.main([]) == 0
    assert capsys.readouterr().out.strip() == "sent 50 replay events"


@pytest.mark.parametrize("missing", ["P10_EVENTHUB_NAMESPACE", "P10_EVENTHUB_NAME"])
def test_a_missing_env_var_exits_2_and_names_only_the_variable(monkeypatch, capsys, missing):
    _cli_env(monkeypatch, FakeSender())
    monkeypatch.delenv(missing)
    assert replay.main([]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == missing


def test_an_unexpected_error_prints_only_the_type_name(monkeypatch, capsys):
    class Failing(FakeSender):
        def send_batch(self, batch):
            raise KeyError("secret-namespace")

    _cli_env(monkeypatch, Failing())
    assert replay.main([]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == "KeyError"


def test_help_says_development_only(capsys):
    with pytest.raises(SystemExit) as exit_info:
        replay.main(["--help"])
    assert exit_info.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "development only" in text.lower()
    assert 'session "replay"' in text
    assert "published figure" in text
    assert "development only" in replay.__doc__.lower()
    assert "published figure" in " ".join(replay.__doc__.split())
