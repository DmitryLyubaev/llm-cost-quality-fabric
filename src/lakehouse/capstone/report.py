"""The capstone's records and report: answers from both routes, scored by rule, rendered as Markdown.

A record is one question asked once on one route (`capstone/record.schema.json`, one JSON object per
line of a `.jsonl` file). `tools/ask_data_agent.py` writes the direct route's; project 3's
`Portfolio.Analyst` writes the Foundry route's with the same schema.

Before any record is written, `redact` replaces URLs with `<url>`, GUIDs with `<id>` and email
addresses with `<email>`. Numbers, dates and labels such as `p3-agent` or `gpt-5.6-luna` are left
alone, so a redacted answer scores as the original would.

`summarise` scores every record with `score_question` (so a count question's verdict filter
applies). The questions scored are the frozen set less any `optional` question with no expected
answer, which is dropped: left out, never counted wrong, and named in the report. The unanswerable
question has no expected answer and is scored. For each route that has records, every scored
question needs exactly one record for each of the `PASSES` passes: a missing one is an error, not a
wrong answer. A route with no records is an error unless it is explicitly allowed to be missing
(`allow_missing`), and is then reported as not run. A record from another freeze, a duplicate
(route, question, pass), a record for an unknown question or one that breaks the schema is an
error too (`ReportError`).

The headline, per route: the mean number correct per pass, out of the number scored; the questions
correct in all passes; and those correct in none.
"""

import json
import re
from pathlib import Path

from lakehouse.capstone.score import Verdict, score_question

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "capstone" / "record.schema.json"
ROUTES = {"direct": "Direct", "foundry": "Through Foundry"}
PASSES = 3


class ReportError(Exception):
    """The records cannot be reported: invalid, from another freeze, duplicated or missing."""


# --- redaction ---

# In this order:
# 1. URLs with a scheme (`abfss://container@account...` too, whole), so a GUID, address or SAS
#    signature inside one goes with it. A URL runs to the first space, quote, angle bracket, backtick
#    or backslash; punctuation that ends a sentence or closes a bracket is given back.
# 2. Credentials become `<token>`, shaped tightly so scored prose is never touched:
#    - `Bearer` followed by a token of at least 20 characters from [\w.~+/-] (and `=` padding); the
#      word stays, so "the bearer of 3 checks" is left alone;
#    - a JWT (`eyJ...`);
#    - SAS parameter values (`sig`, `skoid`, `sktid`, `se`, `sv`, `st`, `sp`, `spr`, `srt`, `ss`),
#      only in a run of `&`-joined parameters that holds a `sig=`; the names stay, so "sp=12 failed"
#      is left alone.
# 3. Email addresses.
# 4. Hosts without a scheme (`api.example.com/v1/...`). Every label holds a letter, so `3.ms` and
#    `1.5.us` are not hosts but `9lake.dfs.core.windows.net` is. A common top-level domain is
#    required, so `gpt-5.6-luna` and `0.75` are not hosts either; and a risky TLD that is also a word
#    or a namespace (ms, ai, azure, microsoft, windows, app, dev) needs two labels before it, so
#    `Microsoft.Azure` and `p2.dev` stay.
# 5. GUIDs: any 8-4-4-4-12 hex run, even inside a longer token (over-redacting an ID is safe and no
#    figure or label has that shape), and a run of exactly 32 hex digits, wherever it stands.
_REST = r"[^\s\"'<>\\`]"
_URL = re.compile(rf"\b(?:https?|wss?|ftp|abfss?)://{_REST}+", re.IGNORECASE)
_URL_TAIL = re.compile(r"[.,;:!?)\]}]+$")
_BEARER = re.compile(r"\b(bearer)\s+[\w.~+/-]{20,}=*", re.IGNORECASE)
_JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*")
_PARAMS = re.compile(r"[\w.-]+=[^&\s\"]*(?:&[\w.-]+=[^&\s\"]*)*")
_SIGNED = re.compile(r"(?:^|&)sig=")
_SAS = re.compile(r"(^|&)(sig|skoid|sktid|se|sv|st|sp|spr|srt|ss)=[^&\s\"]*")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
_SAFE_TLDS = "com|net|org|io|cloud|invalid|local|internal|au|uk|us|eu"
_RISKY_TLDS = "ms|ai|azure|microsoft|windows|app|dev"
_LABEL = r"(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]+\."  # a label that holds a letter
_HOST = re.compile(
    rf"(?<![\w@.-])(?:(?:{_LABEL})+(?:{_SAFE_TLDS})|{_LABEL}(?:{_LABEL})+(?:{_RISKY_TLDS}))"
    rf"\b(?![.-]\w)(?:[/:?#]{_REST}*)?",
    re.IGNORECASE,
)
_GUID = re.compile(r"[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}")
_HEX32 = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])")


def _sas(match: re.Match) -> str:
    """The SAS values in a run of `&`-joined parameters, if the run is signed (holds `sig=`)."""
    run = match.group(0)
    return _SAS.sub(r"\1\2=<token>", run) if _SIGNED.search(run) else run


def _url(match: re.Match) -> str:
    tail = _URL_TAIL.search(match.group(0))
    return "<url>" + (tail.group(0) if tail else "")


def redact(text: str) -> str:
    """`text` with every URL or host as `<url>`, credential as `<token>`, email address as `<email>`
    and GUID as `<id>` (see the comment above for the order and the shapes)."""
    text = _URL.sub(_url, text)
    text = _BEARER.sub(r"\1 <token>", text)
    text = _JWT.sub("<token>", text)
    text = _PARAMS.sub(_sas, text)
    text = _EMAIL.sub("<email>", text)
    text = _HOST.sub(_url, text)
    text = _GUID.sub("<id>", text)
    return _HEX32.sub("<id>", text)


def redact_json(value):
    """A parsed JSON value with `redact` applied to every string in it, keys included."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_json(v) for v in value]
    if isinstance(value, dict):
        return {redact(k): redact_json(v) for k, v in value.items()}
    return value


# --- the record schema (the subset of JSON Schema it uses) ---


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "null": lambda v: v is None,
    "object": lambda v: isinstance(v, dict),
}


def validate_record(record, schema: dict) -> None:
    """ValueError naming the field when `record` breaks `schema`.

    Only the keywords the schema uses are read: type, required, additionalProperties: false, and
    per property type, enum, pattern, minimum and maximum.
    """
    if not isinstance(record, dict):
        raise ValueError("a record is a JSON object")
    missing = [name for name in schema["required"] if name not in record]
    if missing:
        raise ValueError(f"missing fields {missing}")
    properties = schema["properties"]
    if schema.get("additionalProperties") is False:
        extra = sorted(set(record) - set(properties))
        if extra:
            raise ValueError(f"unknown fields {extra}")
    for name, rule in properties.items():
        if name not in record:
            continue
        value = record[name]
        types = rule["type"] if isinstance(rule["type"], list) else [rule["type"]]
        if not any(_TYPES[t](value) for t in types):
            raise ValueError(f"{name} must be {' or '.join(types)}, got {type(value).__name__}")
        if value is None:
            continue
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"{name} must be one of {rule['enum']}, got {value!r}")
        if "pattern" in rule and not re.fullmatch(rule["pattern"], value):
            raise ValueError(f"{name} does not match {rule['pattern']}")
        if "minimum" in rule and value < rule["minimum"]:
            raise ValueError(f"{name} must be at least {rule['minimum']}, got {value}")
        if "maximum" in rule and value > rule["maximum"]:
            raise ValueError(f"{name} must be at most {rule['maximum']}, got {value}")


def load_records(paths: list[Path]) -> list[dict]:
    """Every record in the given `.jsonl` files and folders (a folder's `*.jsonl`, sorted by name).

    A `.jsonl.partial` file (an interrupted run) is not read. ReportError names a bad file and line.
    """
    files = []
    for path in map(Path, paths):
        if path.is_dir():
            files.extend(sorted(path.glob("*.jsonl")))
        elif path.is_file():
            files.append(path)
        else:
            raise ReportError(f"{path.name}: no such file or folder")
    records = []
    for file in files:
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                raise ReportError(f"{file.name} line {number}: not valid JSON") from None
    return records


# --- scoring ---


def scored_questions(questions: list[dict], expected: dict) -> tuple[list[dict], list[str]]:
    """The questions scored, and the ids of those dropped (optional, with no expected answer)."""
    scored, dropped = [], []
    for question in questions:
        if question.get("optional") and question["id"] not in expected:
            dropped.append(question["id"])
        else:
            scored.append(question)
    return scored, dropped


def _check(records: list[dict], questions: list[dict], freeze: dict) -> None:
    schema = load_schema()
    known = {q["id"] for q in questions}
    seen = set()
    for index, record in enumerate(records, start=1):
        try:
            validate_record(record, schema)
        except ValueError as error:
            raise ReportError(f"record {index}: {error}") from None
        if record["freeze"] != freeze["short"]:
            raise ReportError(
                f"record {index} ({record['route']} {record['question_id']} pass {record['pass']}) is from "
                f"another freeze: {record['freeze']}, not {freeze['short']}"
            )
        if record["question_id"] not in known:
            raise ReportError(f"record {index}: unknown question {record['question_id']}")
        key = (record["route"], record["question_id"], record["pass"])
        if key in seen:
            raise ReportError(f"duplicate record: {key[0]} {key[1]} pass {key[2]}")
        seen.add(key)


def summarise(
    records: list[dict], questions: list[dict], expected: dict, freeze: dict, allow_missing=()
) -> dict:
    """Every record checked and scored, and the headline per route (see the module docstring).

    Every route needs records, except a route named in `allow_missing`, which may have none (it is
    then reported as not run). Returns {"scored": [ids], "dropped": [ids], "ignored": n (records
    for dropped questions), "not_run": [routes], "routes": {route: {"records", "verdicts":
    {(id, pass): Verdict}, "per_pass", "mean", "all", "none"}}}, with only the routes that have
    records.
    """
    unknown = sorted(set(allow_missing) - set(ROUTES))
    if unknown:
        raise ReportError(f"unknown routes {unknown}; expected some of {list(ROUTES)}")
    _check(records, questions, freeze)
    scored, dropped = scored_questions(questions, expected)
    for question in scored:
        if question["kind"] != "unanswerable" and question["id"] not in expected:
            raise ReportError(f"{question['id']} is scored but has no expected answer")
    kept = [r for r in records if r["question_id"] not in dropped]
    routes, not_run = {}, []
    for route in ROUTES:
        mine = {(r["question_id"], r["pass"]): r for r in kept if r["route"] == route}
        if not mine:
            if route not in allow_missing:
                raise ReportError(
                    f"no records for the {route} route (allow it with --allow-missing-route {route})"
                )
            not_run.append(route)
            continue
        missing = [(q["id"], n) for n in range(1, PASSES + 1) for q in scored if (q["id"], n) not in mine]
        if missing:
            listed = ", ".join(f"{route} {qid} pass {n}" for qid, n in missing)
            raise ReportError(f"missing records: {listed}")
        verdicts: dict[tuple[str, int], Verdict] = {}
        for question in scored:
            for n in range(1, PASSES + 1):
                answer = mine[(question["id"], n)]["answer_text"]
                verdicts[(question["id"], n)] = score_question(answer, expected.get(question["id"]), question)
        per_pass = [sum(verdicts[(q["id"], n)].correct for q in scored) for n in range(1, PASSES + 1)]
        right = {q["id"]: sum(verdicts[(q["id"], n)].correct for n in range(1, PASSES + 1)) for q in scored}
        routes[route] = {
            "records": mine,
            "verdicts": verdicts,
            "per_pass": per_pass,
            "mean": sum(per_pass) / PASSES,
            "all": [qid for qid, k in right.items() if k == PASSES],
            "none": [qid for qid, k in right.items() if k == 0],
        }
    return {
        "scored": [q["id"] for q in scored],
        "dropped": dropped,
        "ignored": len(records) - len(kept),
        "not_run": not_run,
        "routes": routes,
    }


# --- rendering ---


def _cell(text: str) -> str:
    """Text safe for one Markdown table cell."""
    return redact(str(text)).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _number(value: float) -> str:
    return f"{value:,.1f}" if value != int(value) else f"{int(value):,}"


def _quote(text: str) -> str:
    lines = redact(text).splitlines() or ["(empty answer)"]
    return "\n".join(f"> {line}" if line.strip() else ">" for line in lines)


def _route_section(route: str, data: dict, scored: list[str]) -> list[str]:
    records = list(data["records"].values())
    count = len(records)
    out = [f"### {ROUTES[route]}", ""]
    calls = [r["tool_calls"] for r in records if r["tool_calls"] is not None]
    if calls:
        out.append(f"- Tool calls: {sum(calls):,} in all, {sum(calls) / len(calls):.1f} per question.")
    else:
        out.append("- Tool calls: not recorded.")
    tokens_in = [r["input_tokens"] for r in records if r["input_tokens"] is not None]
    tokens_out = [r["output_tokens"] for r in records if r["output_tokens"] is not None]
    if tokens_in or tokens_out:
        given_in = f"{sum(tokens_in):,} in" if tokens_in else "input not recorded"
        given_out = f"{sum(tokens_out):,} out" if tokens_out else "output not recorded"
        out.append(f"- Tokens: {given_in}, {given_out}.")
    else:
        out.append("- Tokens: not recorded.")
    wall = sum(r["wall_ms"] for r in records) / 1000
    out.append(f"- Wall time: {wall:,.1f} s in all, {wall / count:.1f} s per question.")
    starts = sorted(r["started_utc"] for r in records)
    out.append(f"- First question asked {starts[0]}, last {starts[-1]}.")
    out += [
        "",
        "| Question | Pass | Verdict | Reason | Tool calls | Tokens in | Tokens out | Wall (s) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for qid in scored:
        for n in range(1, PASSES + 1):
            record, verdict = data["records"][(qid, n)], data["verdicts"][(qid, n)]
            cells = [
                qid,
                str(n),
                "right" if verdict.correct else "wrong",
                _cell(verdict.reason),
                *("–" if record[f] is None else f"{record[f]:,}" for f in ("tool_calls", "input_tokens", "output_tokens")),
                f"{record['wall_ms'] / 1000:.1f}",
            ]
            out.append("| " + " | ".join(cells) + " |")
    out.append("")
    return out


def render(
    records: list[dict],
    questions: list[dict],
    expected: dict,
    freeze: dict,
    *,
    snapshot_time: str,
    freezes: int,
    cu: dict | None = None,
    cu_unit: str | None = None,
    allow_missing=(),
) -> str:
    """The report as Markdown (see the module docstring). ReportError if the records are unfit.

    `cu` maps a route to its capacity units read by hand from the Capacity Metrics app (or None),
    in `cu_unit`. `freezes` is how many freezes there were in all.
    `allow_missing` names routes that may have no records; each one is reported as not run.
    """
    summary = summarise(records, questions, expected, freeze, allow_missing)
    scored, dropped, routes = summary["scored"], summary["dropped"], summary["routes"]
    total = len(scored)
    cu = cu or {}
    out = [
        "# Capstone report: the data agent, direct and through Foundry",
        "",
        f"- Freeze: `{freeze['short']}`",
        f"- Snapshot: {snapshot_time}",
        f"- Freezes: {freezes}",
    ]
    scope = f"- Scored: {total} questions are scored, of {len(questions)} in the frozen set"
    if dropped:
        verb = "is" if len(dropped) == 1 else "are"
        scope += f"; {', '.join(dropped)} {verb} dropped (optional, with no expected answer), not counted wrong"
    out.append(scope + ".")
    if summary["ignored"]:
        n = summary["ignored"]
        out.append(f"- {n} record{'s' if n != 1 else ''} for dropped questions {'are' if n != 1 else 'is'} left out.")
    for route in summary["not_run"]:
        out.append(f"- Not run: {ROUTES[route]} has no records (allowed with --allow-missing-route {route}).")
    out.append(f"- Passes: {PASSES} per route. Scoring is by rule (`lakehouse.capstone.score`), with no AI judge.")
    out += [
        "",
        "## Headline",
        "",
        f"| Route | Mean correct per pass | Per pass | Correct in all {PASSES} | Correct in none |",
        "|---|---|---|---|---|",
    ]
    for route, name in ROUTES.items():
        data = routes.get(route)
        if data is None:
            out.append(f"| {name} | not run | – | – | – |")
            continue
        per_pass = ", ".join(str(k) for k in data["per_pass"])
        out.append(f"| {name} | {data['mean']:.2f} of {total} | {per_pass} | {len(data['all'])} | {len(data['none'])} |")
    out += ["", "## Capacity units", ""]
    out.append(f"Read by hand from the Capacity Metrics app, over each run's own time window ({cu_unit or 'no unit given'}).")
    out.append("")
    for route, name in ROUTES.items():
        value = cu.get(route)
        out.append(f"- {name}: {'not given' if value is None else f'{_number(value)} {cu_unit}'}")
    out += ["", "## By question", ""]
    if routes:
        out += ["| Question | Kind | " + " | ".join(f"{ROUTES[r]} (right of {PASSES})" for r in routes) + " |"]
        out += ["|---|---|" + "---|" * len(routes)]
        for qid, kind in ((q["id"], q["kind"]) for q in questions if q["id"] not in dropped):
            counts = [str(sum(routes[r]["verdicts"][(qid, n)].correct for n in range(1, PASSES + 1))) for r in routes]
            out.append(f"| {qid} | {kind} | " + " | ".join(counts) + " |")
    else:
        out.append("No route has records yet.")
    out += ["", "## Verdicts", ""]
    if not routes:
        out += ["No route has records yet.", ""]
    for route, data in routes.items():
        out += _route_section(route, data, scored)
    out += ["## Wrong answers", ""]
    wrong_any = False
    for route, data in routes.items():
        for qid in scored:
            for n in range(1, PASSES + 1):
                verdict = data["verdicts"][(qid, n)]
                if verdict.correct:
                    continue
                wrong_any = True
                out += [f"#### {ROUTES[route]}: {qid}, pass {n}", "", _quote(data["records"][(qid, n)]["answer_text"]), ""]
                out += [f"Reason: {redact(verdict.reason)}", ""]
    if not wrong_any:
        out += ["None.", ""]
    return "\n".join(out).rstrip("\n") + "\n"
