"""The direct-route runner: canned MCP responses through a fake transport, no live call."""

import io
import json
import urllib.error
from pathlib import Path

import pytest

import ask_data_agent
import capstone_freeze
from lakehouse.capstone.freeze import build_freeze
from lakehouse.capstone.report import load_schema, redact, validate_record
from lakehouse.capstone.score import score_question

# A distinctive endpoint: no output, record or message may ever hold it.
ENDPOINT = "https://endpoint-sentinel.example.invalid/v1/mcp/workspaces/ws-sentinel/agent"
INSTRUCTIONS = b"You are the analyst.\n"
SNAPSHOT = "2026-11-08T00:00:00Z"
MODELS = {"direct": "gpt-5.5", "foundry": "gpt-5.5"}
QUESTIONS = [
    {"id": "q01", "text": "How much has project p2 spent?", "kind": "money", "source": {"function": "f"}},
    {"id": "q12", "text": "How many gateway calls?", "kind": "count", "source": {"function": "g"}, "optional": True},
    {"id": "q15", "text": "How did the rate change between versions?", "kind": "unanswerable", "source": None},
]
EXPECTED = {"q01": {"currency": "AUD", "rows": 1, "value": 1234.56}}
GUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
EMAIL = "someone.else@example.org"
LINK = "https://app.fabric.microsoft.com/groups/abc/reports?x=1"


class FakeTransport:
    """Canned MCP over streamable HTTP. `answers` maps question text to the tool's text content."""

    def __init__(self, answers=None, sse=False, status=None, tools=None, rpc_error=None, fail_after=None):
        self.answers = answers or {}
        self.sse = sse
        self.status = status
        self.tools = tools
        self.rpc_error = rpc_error
        self.fail_after = fail_after
        self.calls = []  # (method, params, headers)

    def _reply(self, message_id, result):
        payload = {"jsonrpc": "2.0", "id": message_id, "result": result}
        if self.sse:
            notice = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}
            body = f"event: message\ndata: {json.dumps(notice)}\n\nevent: message\ndata: {json.dumps(payload)}\n\n"
            return 200, {"Content-Type": "text/event-stream"}, body.encode("utf-8")
        return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8")

    def post(self, body: bytes, headers: dict):
        message = json.loads(body)
        method = message["method"]
        self.calls.append((method, message.get("params"), dict(headers)))
        if self.status is not None:
            return self.status, {}, b"denied at " + ENDPOINT.encode()
        if method == "initialize":
            status, head, out = self._reply(message["id"], {"protocolVersion": "2025-06-18", "capabilities": {}})
            return status, {**head, "Mcp-Session-Id": "session-1"}, out
        if method == "notifications/initialized":
            return 202, {}, b""
        if method == "tools/list":
            tools = self.tools if self.tools is not None else [
                {
                    "name": "da_portfolio",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"userQuestion": {"type": "string"}},
                        "required": ["userQuestion"],
                    },
                }
            ]
            return self._reply(message["id"], {"tools": tools})
        if method == "tools/call":
            if self.rpc_error is not None:
                error = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": self.rpc_error, "message": ENDPOINT}}
                return 200, {"Content-Type": "application/json"}, json.dumps(error).encode()
            if self.fail_after is not None and self._tool_calls() > self.fail_after:
                return 503, {}, b""
            question = message["params"]["arguments"]["userQuestion"]
            text = self.answers.get(question, "not in the data")
            return self._reply(message["id"], {"content": [{"type": "text", "text": text}], "isError": False})
        raise AssertionError(f"unexpected method {method}")

    def _tool_calls(self):
        return sum(1 for method, _, _ in self.calls if method == "tools/call")


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    """Freeze inputs on disk, a matching freeze.json, and the CLI arguments for them."""
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "data_agent.json").write_text('{"name": "da_portfolio"}\n', encoding="utf-8")
    questions = tmp_path / "questions.json"
    expected = tmp_path / "expected.json"
    rules = tmp_path / "score.py"
    questions.write_text(json.dumps(QUESTIONS, indent=2) + "\n", encoding="utf-8")
    expected.write_text(json.dumps(EXPECTED, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rules.write_text("# rules\n", encoding="utf-8")
    monkeypatch.setattr(capstone_freeze, "read_instructions", lambda repo, commit: INSTRUCTIONS)
    freeze = build_freeze(questions, expected, SNAPSHOT, agent, INSTRUCTIONS, MODELS, rules=rules)
    freeze_path = tmp_path / "freeze.json"
    freeze_path.write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setenv(ask_data_agent.ENV_URL, ENDPOINT)
    args = [
        "--passes", "3",
        "--out", str(tmp_path / "runs" / "direct"),
        "--questions", str(questions),
        "--expected", str(expected),
        "--rules", str(rules),
        "--data-agent-dir", str(agent),
        "--snapshot-time", SNAPSHOT,
        "--analyst-repo", str(tmp_path),
        "--analyst-commit", "abc123",
        "--model-direct", MODELS["direct"],
        "--model-foundry", MODELS["foundry"],
        "--freeze", str(freeze_path),
    ]  # fmt: skip
    return {"args": args, "freeze": freeze, "out": tmp_path / "runs" / "direct", "questions": questions}


def _records(out: Path) -> list[dict]:
    files = sorted(out.glob("*.jsonl"))
    assert len(files) == 1, files
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


def test_writes_valid_records_for_every_scored_question_and_pass(inputs, capsys):
    fake = FakeTransport({"How much has project p2 spent?": "Project p2 spent A$1,234.56."})

    assert ask_data_agent.main(inputs["args"], transport=fake) == 0

    records = _records(inputs["out"])
    schema = load_schema()
    for record in records:
        validate_record(record, schema)
    assert [(r["question_id"], r["pass"]) for r in records] == [
        ("q01", 1), ("q15", 1), ("q01", 2), ("q15", 2), ("q01", 3), ("q15", 3),
    ]  # fmt: skip
    assert {r["route"] for r in records} == {"direct"}
    assert {r["freeze"] for r in records} == {inputs["freeze"]["short"]}
    assert {r["tool_calls"] for r in records} == {1}
    assert {r["input_tokens"] for r in records} == {None} == {r["output_tokens"] for r in records}
    assert all(isinstance(r["wall_ms"], int) and r["wall_ms"] >= 0 for r in records)
    first = records[0]
    assert first["answer_text"] == "Project p2 spent A$1,234.56."
    assert json.loads(first["raw_output"])["content"][0]["text"] == first["answer_text"]
    assert score_question(first["answer_text"], EXPECTED["q01"], QUESTIONS[0]).correct
    out = capsys.readouterr().out
    assert "q12" in out and "dropped" in out


def test_speaks_mcp_handshake_then_one_call_per_question(inputs):
    fake = FakeTransport()

    ask_data_agent.main(inputs["args"], transport=fake)

    methods = [method for method, _, _ in fake.calls]
    assert methods[:3] == ["initialize", "notifications/initialized", "tools/list"]
    assert methods[3:] == ["tools/call"] * 6
    _, params, first_headers = fake.calls[0]
    assert params["protocolVersion"] and "clientInfo" in params
    assert "Mcp-Session-Id" not in first_headers
    assert "application/json" in first_headers["Accept"] and "text/event-stream" in first_headers["Accept"]
    for _, params, headers in fake.calls[1:]:
        assert headers["Mcp-Session-Id"] == "session-1"
        assert headers["MCP-Protocol-Version"] == "2025-06-18"
    call = fake.calls[3][1]
    assert call == {"name": "da_portfolio", "arguments": {"userQuestion": "How much has project p2 spent?"}}


def test_reads_server_sent_events(inputs):
    fake = FakeTransport({"How much has project p2 spent?": "A$1,234.56"}, sse=True)

    assert ask_data_agent.main(inputs["args"], transport=fake) == 0

    assert _records(inputs["out"])[0]["answer_text"] == "A$1,234.56"


def test_redacts_a_url_a_guid_and_an_email_before_writing(inputs):
    leaky = f"Source {LINK} item {GUID}, ask {EMAIL}. Total A$1,234.56 for p2."
    fake = FakeTransport({"How much has project p2 spent?": leaky})

    ask_data_agent.main(inputs["args"], transport=fake)

    raw = next(inputs["out"].glob("*.jsonl")).read_text(encoding="utf-8")
    for secret in (LINK, "app.fabric.microsoft.com", GUID, EMAIL, "example.org"):
        assert secret not in raw
    record = _records(inputs["out"])[0]
    assert record["answer_text"] == "Source <url> item <id>, ask <email>. Total A$1,234.56 for p2."
    assert json.loads(record["raw_output"])["content"][0]["text"] == record["answer_text"]


@pytest.mark.parametrize(
    "text",
    [
        "Project p2 spent A$1,234.56 (AUD 1 234.56) in October 2026.",
        "The p3-agent study's gpt-5.6-luna success rate was 98.3%, or 0.983, 59 of 60.",
        "p2-retrieval arm E3: top-1 rate 0.75; text-embedding-3-large had 1.2k results.",
        "From 2026-10-06 to 2026-10-09 project p4 cost 0.0047 USD; untagged and releaselens-shared too.",
        "Projects p1, p4 & p7 had failures; -A$3.40 refund; 10/25/2026 14:05; 5-7 days; 1.5x.",
        "fabric@ ran it; version 2 vs v1.2.3.",
        "e.g. the p7 checks: i.e. 3 failed. Rate 0.995 vs .75; ratio 3.2.1-beta; file report.md.",
    ],
)
def test_redaction_leaves_numbers_and_project_labels_alone(text):
    assert redact(text) == text


def test_redaction_patterns():
    assert redact(f"see {LINK}.") == "see <url>."
    assert redact(f"({LINK})") == "(<url>)"
    assert redact(GUID.upper()) == "<id>"
    assert redact(f"{{{GUID}}}") == "{<id>}"
    assert redact(f"mail {EMAIL}, now") == "mail <email>, now"
    assert redact(f"https://x.example/a/{GUID}?u={EMAIL}") == "<url>"
    assert redact(f"{EMAIL} {GUID} {LINK}") == "<email> <id> <url>"
    assert redact(f"item-{GUID}") == "item-<id>"
    assert redact(f"ws{GUID}x") == "ws<id>x"
    assert redact("POST api.fabric.example.com/v1/mcp/workspaces/w/agent failed.") == "POST <url> failed."
    assert redact("host leak.example.org, then") == "host <url>, then"


def test_refuses_to_start_when_the_freeze_is_broken(inputs, capsys):
    inputs["questions"].write_text(json.dumps(QUESTIONS[:1]) + "\n", encoding="utf-8")
    fake = FakeTransport()

    assert ask_data_agent.main(inputs["args"], transport=fake) == 1

    assert fake.calls == []
    assert not inputs["out"].exists()
    assert "questions" in capsys.readouterr().err


def test_refuses_without_the_endpoint_variable(inputs, capsys, monkeypatch):
    monkeypatch.delenv(ask_data_agent.ENV_URL)
    fake = FakeTransport()

    assert ask_data_agent.main(inputs["args"], transport=fake) == 2

    assert fake.calls == []
    assert ask_data_agent.ENV_URL in capsys.readouterr().err


def _assert_no_endpoint(capsys, out: Path):
    captured = capsys.readouterr()
    for text in (captured.out, captured.err):
        assert ENDPOINT not in text and "endpoint-sentinel" not in text and "ws-sentinel" not in text
    for path in out.rglob("*") if out.exists() else []:
        if path.is_file():
            assert "endpoint-sentinel" not in path.read_text(encoding="utf-8")
    return captured


def test_never_prints_the_endpoint_on_success(inputs, capsys):
    assert ask_data_agent.main(inputs["args"], transport=FakeTransport()) == 0
    _assert_no_endpoint(capsys, inputs["out"])


def test_an_http_error_gives_the_status_and_a_code_only(inputs, capsys):
    assert ask_data_agent.main(inputs["args"], transport=FakeTransport(status=401)) == 3
    err = _assert_no_endpoint(capsys, inputs["out"]).err
    assert "401" in err and "http" in err


def test_a_json_rpc_error_gives_its_code_only(inputs, capsys):
    assert ask_data_agent.main(inputs["args"], transport=FakeTransport(rpc_error=-32603)) == 3
    err = _assert_no_endpoint(capsys, inputs["out"]).err
    assert "-32603" in err


def test_a_failure_mid_run_leaves_no_complete_record_file(inputs, capsys):
    assert ask_data_agent.main(inputs["args"], transport=FakeTransport(fail_after=2)) == 3
    assert list(inputs["out"].glob("*.jsonl")) == []
    assert len(list(inputs["out"].glob("*.jsonl.partial"))) == 1
    assert "503" in _assert_no_endpoint(capsys, inputs["out"]).err


@pytest.mark.parametrize("tools", [[], [{"name": "a", "inputSchema": {}}, {"name": "b", "inputSchema": {}}]])
def test_the_agent_must_expose_exactly_one_tool(inputs, capsys, tools):
    assert ask_data_agent.main(inputs["args"], transport=FakeTransport(tools=tools)) == 3
    assert "tool" in capsys.readouterr().err


def test_the_question_argument_is_the_single_string_property():
    tool = {"name": "t", "inputSchema": {"properties": {"q": {"type": "string"}, "n": {"type": "integer"}}}}
    assert ask_data_agent.question_argument(tool) == "q"
    with pytest.raises(ask_data_agent.McpError):
        ask_data_agent.question_argument({"name": "t", "inputSchema": {"properties": {"a": {"type": "string"}, "b": {"type": "string"}}}})
    required = {"properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["b"]}
    assert ask_data_agent.question_argument({"name": "t", "inputSchema": required}) == "b"


def test_answer_text_joins_the_text_content_only():
    result = {"content": [{"type": "text", "text": "one"}, {"type": "image", "data": "x"}, {"type": "text", "text": "two"}]}
    assert ask_data_agent.answer_text(result) == "one\ntwo"


# --- the real transport and token, with urllib and the credential faked ---


class _Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "application/json", "Mcp-Session-Id": "s"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_http_transport_posts_with_a_bearer_token(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen["url"], seen["headers"], seen["body"] = request.full_url, dict(request.header_items()), request.data
        return _Response(b"{}")

    monkeypatch.setattr(ask_data_agent.urllib.request, "urlopen", urlopen)
    transport = ask_data_agent.HttpTransport(ENDPOINT, lambda: "tok-123", timeout=5)

    status, headers, body = transport.post(b'{"x": 1}', {"Accept": "application/json"})

    assert (status, body) == (200, b"{}")
    assert headers["Mcp-Session-Id"] == "s"
    assert seen["url"] == ENDPOINT and seen["body"] == b'{"x": 1}'
    assert seen["headers"]["Authorization"] == "Bearer tok-123"


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError(ENDPOINT, 403, f"Forbidden {ENDPOINT}", {}, io.BytesIO(ENDPOINT.encode())),
        urllib.error.URLError(f"cannot reach {ENDPOINT}"),
        TimeoutError(f"timed out {ENDPOINT}"),
    ],
)
def test_http_transport_errors_never_name_the_endpoint(monkeypatch, error):
    def urlopen(request, timeout):
        raise error

    monkeypatch.setattr(ask_data_agent.urllib.request, "urlopen", urlopen)
    transport = ask_data_agent.HttpTransport(ENDPOINT, lambda: "tok-123", timeout=5)

    if isinstance(error, urllib.error.HTTPError):
        status, _, _ = transport.post(b"{}", {})
        assert status == 403
        return
    with pytest.raises(ask_data_agent.McpError) as raised:
        transport.post(b"{}", {})
    assert "endpoint-sentinel" not in str(raised.value) and raised.value.__cause__ is None


class _Token:
    def __init__(self, token, expires_on):
        self.token, self.expires_on = token, expires_on


class _Credential:
    def __init__(self):
        self.scopes = []

    def get_token(self, *scopes):
        self.scopes.append(scopes)
        return _Token(f"tok-{len(self.scopes)}", 1000 + 3600 * len(self.scopes))


def test_the_token_is_for_the_fabric_scope_and_refreshed_before_expiry():
    credential = _Credential()
    now = [1000.0]
    token = ask_data_agent.CliToken(credential, clock=lambda: now[0])

    assert token() == "tok-1"
    assert token() == "tok-1"
    now[0] = 1000 + 3600 - 60  # within the refresh margin
    assert token() == "tok-2"
    assert credential.scopes == [("https://api.fabric.microsoft.com/.default",)] * 2


def test_the_schema_requires_every_record_field():
    schema = load_schema()
    assert schema["required"] == [
        "route", "freeze", "question_id", "pass", "answer_text", "raw_output",
        "tool_calls", "input_tokens", "output_tokens", "wall_ms", "started_utc",
    ]  # fmt: skip
    assert schema["additionalProperties"] is False
