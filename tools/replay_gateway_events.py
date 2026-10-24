"""Replay generated gateway events into the Event Hub: development only.

Its events carry session "replay" and never feed a published figure. Run it to test the
Event Hub -> Eventhouse path before a live session, never to produce data that is counted.

Environment: P10_EVENTHUB_NAMESPACE (the Event Hubs namespace name, not the host) and
P10_EVENTHUB_NAME (the hub). Both are required; the script prints neither. Sign in first with
`az login`: the sender uses AzureCliCredential. Install the extra with `pip install -e ".[replay]"`.
"""
import argparse
import json
import logging
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

from lakehouse.gateway.contract import BACKENDS, CALLERS

NOTICE = 'Development only: its events carry session "replay" and never feed a published figure.'

SESSION = "replay"
# The ReleaseLens gateway's API name and its one operation.
API = "azure-openai-v1"
OPERATION = "chat-completions"
REFUSAL_STATUSES = (401, 403, 429)
MAX_STEP_MS = 1500  # the longest gap between two events
INSTALL_HINT = "install the replay extra: pip install -e .[replay]"

ENV_NAMESPACE = "P10_EVENTHUB_NAMESPACE"
ENV_HUB = "P10_EVENTHUB_NAME"


class MissingExtraError(Exception):
    """The replay extra (azure-eventhub, azure-identity) is not installed."""


def _event_time(moment: datetime) -> str:
    """ISO 8601 UTC with milliseconds and a Z suffix, like the policy's yyyy-MM-ddTHH:mm:ss.fffZ."""
    moment = moment.astimezone(timezone.utc)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def make_events(n: int, start: datetime, seed: int) -> list[dict]:
    """Return `n` valid gateway events, deterministic for `seed`, with increasing event_time.

    The first events cover every caller and both backends, and every tenth event is a refusal
    (401, 403 or 429, answered_by and tokens null), so a count of 10 or more holds all of them. A
    502 (every twentieth event from the fifteenth, and now and then another) is a backend failure
    that reached on-error: answered_by null, tokens null, not refused. The last event is at most
    `MAX_STEP_MS * n` after `start`.
    """
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    rng = random.Random(seed)
    moment = start.astimezone(timezone.utc)
    moment = moment.replace(microsecond=moment.microsecond // 1000 * 1000)
    events = []
    for i in range(n):
        moment += timedelta(milliseconds=rng.randint(1, MAX_STEP_MS))
        refused = i % 10 == 9
        failed = not refused and i > 1 and (i % 20 == 14 or rng.random() < 0.03)
        caller = CALLERS[i] if i < len(CALLERS) else rng.choice(CALLERS)
        if refused or failed:
            answered_by = None
        else:
            answered_by = BACKENDS[i] if i < len(BACKENDS) else rng.choice(BACKENDS)
        if refused:
            status, latency = rng.choice(REFUSAL_STATUSES), rng.randint(2, 40)
        elif failed:
            status, latency = 502, rng.randint(1000, 8000)
        else:
            status, latency = 200, rng.randint(150, 4000)
        events.append(
            {
                "event_time": _event_time(moment),
                "request_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
                "session": SESSION,
                "api": API,
                "operation": OPERATION,
                "caller": caller,
                "answered_by": answered_by,
                "status": status,
                "refused_by_gateway": refused,
                "tokens": None if answered_by is None else rng.randint(20, 2000),
                "latency_ms": latency,
            }
        )
    return events


def _host(namespace: str) -> str:
    # Built at run time: the hygiene check forbids a literal Event Hubs namespace host in git.
    return f"{namespace}." + "servicebus" + ".windows.net"


class _HubSender:
    """Adapts EventHubProducerClient to the sender the script uses: `add` takes the JSON text."""

    class _Batch:
        def __init__(self, batch, event_data):
            self.batch = batch
            self._event_data = event_data

        def add(self, body: str) -> None:
            self.batch.add(self._event_data(body))

    def __init__(self, client, event_data, credential):
        self._client = client
        self._event_data = event_data
        self._credential = credential

    def create_batch(self):
        return self._Batch(self._client.create_batch(), self._event_data)

    def send_batch(self, batch) -> None:
        self._client.send_batch(batch.batch)

    def close(self) -> None:
        try:
            self._client.close()
        finally:
            self._credential.close()


def _make_sender(namespace: str, hub: str):
    """The default sender. The azure imports stay here so the tests need no azure-eventhub."""
    try:
        from azure.eventhub import EventData, EventHubProducerClient
        from azure.identity import AzureCliCredential
    except ImportError:
        # Only the import of the extra means "not installed"; any other ImportError is a real failure.
        raise MissingExtraError from None

    credential = AzureCliCredential()
    try:
        client = EventHubProducerClient(
            fully_qualified_namespace=_host(namespace),
            eventhub_name=hub,
            credential=credential,
        )
    except BaseException:
        credential.close()
        raise
    return _HubSender(client, EventData, credential)


def send(events: list[dict], namespace: str, hub: str, sender=None) -> int:
    """Send `events` in as few batches as fit, close the sender and return the number sent.

    A sender has create_batch(), send_batch(batch) and close(); a batch has add(json_text), which
    raises ValueError when it is full. If sending fails, the exception is re-raised with
    `events_sent` set to the number of events in batches sent before the failure.
    """
    sent = 0
    try:
        sender = sender if sender is not None else _make_sender(namespace, hub)
        try:
            batch = sender.create_batch()
            in_batch = 0
            for event in events:
                body = json.dumps(event, separators=(",", ":"))
                try:
                    batch.add(body)
                except ValueError:
                    if in_batch == 0:
                        raise  # one event does not fit an empty batch
                    sender.send_batch(batch)
                    sent += in_batch
                    batch = sender.create_batch()
                    batch.add(body)
                    in_batch = 0
                in_batch += 1
            if in_batch:
                sender.send_batch(batch)
                sent += in_batch
        finally:
            sender.close()
    except Exception as error:
        error.events_sent = sent
        raise
    return sent


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def main(argv: list[str] | None = None) -> int:
    # The SDK's warnings can carry the namespace host, and with no handler configured Python's
    # last-resort handler would print them to stderr.
    azure_logger = logging.getLogger("azure")
    azure_logger.setLevel(logging.CRITICAL + 1)
    azure_logger.propagate = False
    parser = argparse.ArgumentParser(description=NOTICE)
    parser.add_argument("--count", type=_positive_int, default=50, help="events to send (default 50)")
    parser.add_argument("--seed", type=int, default=1, help="random seed (default 1)")
    args = parser.parse_args(argv)

    namespace = os.environ.get(ENV_NAMESPACE)
    hub = os.environ.get(ENV_HUB)
    for name, value in ((ENV_NAMESPACE, namespace), (ENV_HUB, hub)):
        if not value:
            print(name, file=sys.stderr)
            return 2
    if "." in namespace:
        print(f"{ENV_NAMESPACE} must be a namespace name, not a host", file=sys.stderr)
        return 2
    try:
        # Start early enough that the last event is not in the future.
        now = datetime.now(timezone.utc)
        events = make_events(args.count, now - timedelta(milliseconds=MAX_STEP_MS * args.count), args.seed)
        sent = send(events, namespace, hub)
    except MissingExtraError:
        print(INSTALL_HINT, file=sys.stderr)
        return 1
    except Exception as error:  # never print the message: it may hold the namespace or hub
        print(type(error).__name__, file=sys.stderr)
        print(f"sent {getattr(error, 'events_sent', 0)} replay events before the failure", file=sys.stderr)
        return 1
    print(f"sent {sent} replay events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
