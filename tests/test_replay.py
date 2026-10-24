"""Tests for the development-only gateway event replay (tools/replay_gateway_events.py).

No test imports azure-eventhub: `send` takes an injected sender, and the CLI is driven through the
module's `_make_sender` seam.
"""
import json
import logging
import re
import subprocess
import sys
import types
from pathlib import Path
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
    # A backend failure that reached on-error: not refused, but nothing answered.
    assert any(not e["refused_by_gateway"] and e["answered_by"] is None for e in events)
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
    code = (
        "import sys; sys.path.insert(0, 'tools'); import replay_gateway_events; "
        "assert 'azure.eventhub' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_an_oversize_event_raises_sends_nothing_and_closes_the_sender():
    sender = FakeSender(capacity=0)
    with pytest.raises(ValueError):
        replay.send(replay.make_events(3, START, seed=1), "ns", "hub", sender=sender)
    assert sender.sent == []
    assert sender.closed


def test_a_failing_retry_after_a_full_batch_raises_and_closes_the_sender():
    class OneBatchThenNone(FakeSender):
        def create_batch(self):
            batch = FakeBatch(self.capacity)
            self.capacity = 0  # every later batch holds nothing
            return batch

    sender = OneBatchThenNone(capacity=2)
    with pytest.raises(ValueError):
        replay.send(replay.make_events(5, START, seed=1), "ns", "hub", sender=sender)
    assert [len(batch) for batch in sender.sent] == [2]
    assert sender.closed


class _FakeAzure:
    """Stands in for azure.eventhub and azure.identity, and records what the real sender does."""

    def __init__(self, monkeypatch, fail_client=False):
        outer = self
        self.clients, self.credentials, self.bodies = [], [], []

        class EventData:
            def __init__(self, body):
                outer.bodies.append(body)

        class Batch:
            def __init__(self):
                self.items = []

            def add(self, item):
                self.items.append(item)

        class EventHubProducerClient:
            def __init__(self, **kwargs):
                if fail_client:
                    raise RuntimeError("cannot build the client")
                self.kwargs = kwargs
                self.batches, self.closed = [], False
                outer.clients.append(self)

            def create_batch(self):
                return Batch()

            def send_batch(self, batch):
                self.batches.append(batch)

            def close(self):
                self.closed = True

        class AzureCliCredential:
            def __init__(self):
                self.closed = False
                outer.credentials.append(self)

            def close(self):
                self.closed = True

        eventhub = types.ModuleType("azure.eventhub")
        eventhub.EventData, eventhub.EventHubProducerClient = EventData, EventHubProducerClient
        identity = types.ModuleType("azure.identity")
        identity.AzureCliCredential = AzureCliCredential
        monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
        monkeypatch.setitem(sys.modules, "azure.eventhub", eventhub)
        monkeypatch.setitem(sys.modules, "azure.identity", identity)


def test_the_real_client_wrapper_builds_the_client_and_forwards_the_events_and_close(monkeypatch):
    fake = _FakeAzure(monkeypatch)
    events = replay.make_events(3, START, seed=1)
    assert replay.send(events, "ns1", "hub1") == 3
    (client,) = fake.clients
    (credential,) = fake.credentials
    assert set(client.kwargs) == {"fully_qualified_namespace", "eventhub_name", "credential"}
    assert client.kwargs["fully_qualified_namespace"] == "ns1" + ".servicebus" + ".windows.net"
    assert client.kwargs["eventhub_name"] == "hub1"
    assert client.kwargs["credential"] is credential
    assert [json.loads(body) for body in fake.bodies] == events
    assert [len(batch.items) for batch in client.batches] == [3]
    assert client.closed
    assert credential.closed


def test_the_credential_is_closed_when_the_client_constructor_raises(monkeypatch):
    fake = _FakeAzure(monkeypatch, fail_client=True)
    with pytest.raises(RuntimeError):
        replay.send(replay.make_events(3, START, seed=1), "ns1", "hub1")
    (credential,) = fake.credentials
    assert credential.closed
    assert fake.clients == []


def test_a_missing_extra_import_raises_the_dedicated_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.eventhub", None)  # makes `import azure.eventhub` raise ImportError
    with pytest.raises(replay.MissingExtraError):
        replay._make_sender("ns1", "hub1")


def test_send_reports_the_events_sent_before_a_failure():
    class FailsOnSecondBatch(FakeSender):
        def send_batch(self, batch):
            if self.sent:
                raise RuntimeError("boom")
            super().send_batch(batch)

    sender = FailsOnSecondBatch(capacity=4)
    with pytest.raises(RuntimeError) as error_info:
        replay.send(replay.make_events(10, START, seed=1), "ns", "hub", sender=sender)
    assert error_info.value.events_sent == 4
    assert sender.closed


def test_send_reports_zero_when_the_first_batch_fails():
    class Failing(FakeSender):
        def send_batch(self, batch):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError) as error_info:
        replay.send(replay.make_events(3, START, seed=1), "ns", "hub", sender=Failing())
    assert error_info.value.events_sent == 0


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
    assert err.splitlines()[0] == "KeyError"
    assert "secret" not in err


def test_a_failure_partway_prints_the_type_name_and_how_many_events_were_sent(monkeypatch, capsys):
    class FailsOnSecondBatch(FakeSender):
        def send_batch(self, batch):
            if self.sent:
                raise KeyError("secret-namespace")
            super().send_batch(batch)

    _cli_env(monkeypatch, FailsOnSecondBatch(capacity=4))
    assert replay.main(["--count", "10"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.splitlines() == ["KeyError", "sent 4 replay events before the failure"]


def test_a_failure_before_any_send_reports_zero_events(monkeypatch, capsys):
    class Failing(FakeSender):
        def send_batch(self, batch):
            raise KeyError("secret-namespace")

    _cli_env(monkeypatch, Failing())
    assert replay.main([]) == 1
    assert capsys.readouterr().err.splitlines()[1] == "sent 0 replay events before the failure"


def test_an_import_error_inside_send_is_not_reported_as_a_missing_extra(monkeypatch, capsys):
    class Failing(FakeSender):
        def send_batch(self, batch):
            raise ImportError("cannot import name x from secret-namespace")

    _cli_env(monkeypatch, Failing())
    assert replay.main([]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.splitlines()[0] == "ImportError"
    assert "install the replay extra" not in err
    assert "secret" not in err


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


@pytest.fixture
def azure_logger_state():
    logger = logging.getLogger("azure")
    saved = (logger.level, logger.propagate)
    yield logger
    logger.setLevel(saved[0])
    logger.propagate = saved[1]


def test_the_cli_silences_the_sdk_loggers(monkeypatch, capsys, azure_logger_state):
    host = "secret-namespace" + ".servicebus" + ".windows.net"

    class Warning_(FakeSender):
        def send_batch(self, batch):
            logging.getLogger("azure.eventhub.x").warning("could not reach %s", host)
            super().send_batch(batch)

    _cli_env(monkeypatch, Warning_())
    # An application-configured handler on the root logger, as the SDK's warnings would reach.
    root_handler = logging.StreamHandler(sys.stderr)
    logging.getLogger().addHandler(root_handler)
    try:
        assert replay.main([]) == 0
    finally:
        logging.getLogger().removeHandler(root_handler)
    out, err = capsys.readouterr()
    assert "secret-namespace" not in out + err
    assert err == ""


def test_the_cli_rejects_a_namespace_that_is_a_host(monkeypatch, capsys):
    _cli_env(monkeypatch, FakeSender())
    monkeypatch.setenv("P10_EVENTHUB_NAMESPACE", "secret-namespace" + ".servicebus" + ".windows.net")
    assert replay.main([]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "P10_EVENTHUB_NAMESPACE" in err
    assert "secret-namespace" not in err


def test_a_missing_extra_prints_the_install_hint(monkeypatch, capsys):
    _cli_env(monkeypatch, FakeSender())

    def no_extra(namespace, hub):
        raise replay.MissingExtraError

    monkeypatch.setattr(replay, "_make_sender", no_extra)
    assert replay.main([]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == "install the replay extra: pip install -e .[replay]"


def test_the_cli_does_not_produce_future_event_times(monkeypatch, capsys):
    sender = FakeSender()
    _cli_env(monkeypatch, sender)
    before = datetime.now(timezone.utc)
    assert replay.main(["--count", "5000"]) == 0
    sent = [json.loads(item) for batch in sender.sent for item in batch]
    last = datetime.fromisoformat(sent[-1]["event_time"])
    assert last <= datetime.now(timezone.utc)
    assert before - last < timedelta(milliseconds=replay.MAX_STEP_MS * 5000)
