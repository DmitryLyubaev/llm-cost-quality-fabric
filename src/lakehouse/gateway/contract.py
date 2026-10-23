"""The gateway event contract: one JSON object per gateway call.

`EVENT_FIELDS` is the single source of truth for the event's shape. `contracts/gateway-event.schema.json`
mirrors it (a test keeps the two in step), and the gateway policy, the KQL table and the replay
generator are all written against it. Types are `datetime` (an ISO 8601 string with an offset, `Z`
or `+hh:mm`), `string`, `int` and `bool`.

The event carries no client IP, no request or response body and no deployment or model field.
`caller` is a label, never an object ID, and no string value may be shaped like a GUID, except
`request_id`: API Management's `context.RequestId` is a random per-request GUID (not a user, tenant
or resource identifier) and the dedup needs it as is.
A call the gateway itself refused has `answered_by` and `tokens` null. A null `answered_by` also
forces `tokens` null, but a call that was not refused may still have a null `answered_by`: a backend
failure or timeout that reached `on-error` (for example status 502).
"""
import re
from datetime import datetime

EVENT_FIELDS: list[tuple[str, str]] = [
    ("event_time", "datetime"),
    ("request_id", "string"),
    ("session", "string"),
    ("api", "string"),
    ("operation", "string"),
    ("caller", "string"),
    ("answered_by", "string"),
    ("status", "int"),
    ("refused_by_gateway", "bool"),
    ("tokens", "int"),
    ("latency_ms", "int"),
]

CALLERS = ("owner", "deploy", "webapp", "other")
BACKENDS = ("primary", "secondary")

# Fields that may be null (see the module docstring for when).
NULLABLE = ("answered_by", "tokens")

# The one field allowed to hold a GUID.
GUID_EXEMPT = "request_id"

_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})")
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def _is_datetime(value: object) -> bool:
    if not isinstance(value, str) or not _DATETIME.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


# bool is a subclass of int, so each check excludes it explicitly where it must not pass.
_TYPE_CHECKS = {
    "datetime": _is_datetime,
    "string": lambda value: isinstance(value, str),
    "int": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "bool": lambda value: isinstance(value, bool),
}


def validate_event(event: dict) -> list[str]:
    """Return the problems with `event`; an empty list means it is valid."""
    if not isinstance(event, dict):
        return ["event must be an object"]

    names = [name for name, _ in EVENT_FIELDS]
    problems = [f"missing field {name!r}" for name in names if name not in event]
    problems += [f"unknown field {key!r}" for key in event if key not in names]

    for name, kind in EVENT_FIELDS:
        if name not in event:
            continue
        value = event[name]
        if value is None:
            if name not in NULLABLE:
                problems.append(f"{name} must not be null")
        elif not _TYPE_CHECKS[kind](value):
            problems.append(f"{name} must be a {kind}, got {value!r}")

    caller = event.get("caller")
    if caller is not None and caller not in CALLERS:
        problems.append(f"caller must be one of {', '.join(CALLERS)}, got {caller!r}")
    answered_by = event.get("answered_by")
    if answered_by is not None and answered_by not in BACKENDS:
        problems.append(f"answered_by must be one of {', '.join(BACKENDS)} or null, got {answered_by!r}")

    for name, value in event.items():
        if name != GUID_EXEMPT and isinstance(value, str) and _GUID.search(value):
            problems.append(f"{name} holds a GUID-shaped value")

    if event.get("refused_by_gateway") is True:
        for name in NULLABLE:
            if event.get(name) is not None:
                problems.append(f"{name} must be null when refused_by_gateway is true")
    if "answered_by" in event and event["answered_by"] is None and event.get("tokens") is not None:
        problems.append("tokens must be null when answered_by is null")
    return problems
