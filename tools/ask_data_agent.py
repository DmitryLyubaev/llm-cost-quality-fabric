"""Ask the frozen questions directly through the data agent's MCP endpoint: the capstone's direct route.

    python tools/ask_data_agent.py --passes 3 --out capstone/runs/direct \\
        --analyst-repo <path> --analyst-commit <sha> \\
        --snapshot-time <iso> --model-direct <name> --model-foundry <name>

It takes the same freeze options as tools/capstone_freeze.py and refuses to start (exit 1, naming
the component) unless verify_freeze passes on them. The endpoint is read from the environment
variable CAPSTONE_MCP_URL and is never printed: error messages give the HTTP status and a code only.
Sign in first with `az login` as fabric@: the token for https://api.fabric.microsoft.com/.default
comes from AzureCliCredential (`pip install azure-identity`; the deploy extra has it).

MCP over streamable HTTP, JSON-RPC 2.0: `initialize`, the `notifications/initialized` notice,
`tools/list` (the data agent exposes exactly one tool), then one `tools/call` per question per pass.
A response may be JSON or server-sent events. The answer is the tool result's text content. A
JSON-RPC error or a tool result flagged `isError` stops the run (exit 3): it is a failed call, not an
answer, so it is never scored as a wrong one.

Each record (capstone/record.schema.json) is written as soon as its answer arrives, to
`<out>/direct-<freeze short>-<UTC start>.jsonl.partial`, renamed to `.jsonl` when every pass is
done, so an interrupted run never looks complete. An optional question with no expected answer
(q12, if its gateway session never ran) is dropped and not asked. `answer_text` and `raw_output`
are redacted (URLs, GUIDs and emails) before writing.

Exit codes: 0 done; 1 the freeze is broken; 2 a usage or input error; 3 the endpoint or the token
failed.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import capstone_freeze
from capstone_freeze import InputError, add_freeze_arguments, load_verified_freeze
from lakehouse.capstone.freeze import DataAgentError, FreezeBroken
from lakehouse.capstone.report import load_schema, redact, redact_json, scored_questions, validate_record

ENV_URL = "CAPSTONE_MCP_URL"
SCOPE = "https://api.fabric.microsoft.com/.default"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "ask_data_agent", "version": "1"}
REFRESH_MARGIN_S = 300  # a token this close to expiry is renewed before the next call
DEFAULT_TIMEOUT_S = 300
INSTALL_HINT = "azure-identity is not installed: pip install azure-identity (the deploy extra has it)"


class McpError(Exception):
    """The endpoint or the token failed. The message holds a status and a code, never a URL."""

    def __init__(self, status: int | None, code: str):
        self.status, self.code = status, code
        where = f"HTTP {status}, " if status is not None else ""
        super().__init__(f"the data agent's MCP endpoint failed: {where}code {code}")


class CliToken:
    """A bearer token for the Fabric scope from the Azure CLI, renewed shortly before it expires."""

    def __init__(self, credential=None, clock=time.time):
        self._credential = credential
        self._clock = clock
        self._token = None

    def __call__(self) -> str:
        if self._token is None or self._token.expires_on - self._clock() < REFRESH_MARGIN_S:
            if self._credential is None:
                try:
                    from azure.identity import AzureCliCredential
                except ImportError:
                    raise InputError(INSTALL_HINT) from None
                self._credential = AzureCliCredential()
            try:
                self._token = self._credential.get_token(SCOPE)
            except Exception:  # the CLI's own message may name the tenant or the account
                raise McpError(None, "token (az login as fabric@)") from None
        return self._token.token


class HttpTransport:
    """POSTs one JSON-RPC message to the endpoint: (status, headers, body). Never names the URL."""

    def __init__(self, url: str, token, timeout: float = DEFAULT_TIMEOUT_S):
        self._url, self._token, self._timeout = url, token, timeout

    def post(self, body: bytes, headers: dict) -> tuple[int, dict, bytes]:
        request = urllib.request.Request(
            self._url, data=body, method="POST", headers={**headers, "Authorization": f"Bearer {self._token()}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers.items()) if error.headers else {}, error.read() or b""
        except (urllib.error.URLError, OSError):
            raise McpError(None, "network") from None


def _lower(headers: dict) -> dict:
    return {str(k).lower(): v for k, v in (headers or {}).items()}


def _messages(content_type: str, body: bytes) -> list:
    """The JSON-RPC messages in a response body: plain JSON, or the `data:` of each SSE event."""
    text = body.decode("utf-8")
    if "text/event-stream" not in content_type:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else [parsed]
    messages, data = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif not line and data:
            messages.append(json.loads("\n".join(data)))
            data = []
    return messages


class McpClient:
    """A minimal MCP client over a transport with `post(body, headers) -> (status, headers, body)`."""

    def __init__(self, transport):
        self._transport = transport
        self._session = None
        self._protocol = None
        self._next_id = 0

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        if self._protocol:
            headers["MCP-Protocol-Version"] = self._protocol
        return headers

    def _post(self, message: dict) -> tuple[int, dict, bytes]:
        status, headers, body = self._transport.post(json.dumps(message).encode("utf-8"), self._headers())
        if not 200 <= status < 300:
            raise McpError(status, "http-error")
        return status, _lower(headers), body

    def request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        message_id = self._next_id
        status, headers, body = self._post({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        if not self._session and headers.get("mcp-session-id"):
            self._session = headers["mcp-session-id"]
        try:
            messages = _messages(headers.get("content-type", ""), body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise McpError(status, "bad-response") from None
        for message in messages:
            if isinstance(message, dict) and message.get("id") == message_id and "method" not in message:
                if "error" in message:
                    code = (message["error"] or {}).get("code")
                    raise McpError(status, f"rpc{code if isinstance(code, int) else '-unknown'}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise McpError(status, "bad-response")
                return result
        raise McpError(status, "no-response")

    def notify(self, method: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method})

    def initialize(self) -> None:
        result = self.request(
            "initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO}
        )
        self._protocol = result.get("protocolVersion") or PROTOCOL_VERSION
        self.notify("notifications/initialized")

    def the_tool(self) -> dict:
        tools = self.request("tools/list", {}).get("tools")
        if not isinstance(tools, list) or len(tools) != 1 or not isinstance(tools[0], dict):
            raise McpError(None, "tool-count (the data agent should expose exactly one tool)")
        return tools[0]

    def call(self, name: str, arguments: dict) -> dict:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise McpError(None, "tool-error")
        return result


def question_argument(tool: dict) -> str:
    """The tool's argument that takes the question: its one required string, else its one string."""
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    strings = [name for name, rule in properties.items() if isinstance(rule, dict) and rule.get("type") == "string"]
    required = [name for name in strings if name in (schema.get("required") or [])]
    for candidates in (required, strings):
        if len(candidates) == 1:
            return candidates[0]
    raise McpError(None, "tool-schema (no single string argument for the question)")


def answer_text(result: dict) -> str:
    """The tool result's text content, its text items joined by line breaks."""
    return "\n".join(
        item["text"]
        for item in result.get("content") or []
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
    )


def _utc(moment: datetime) -> str:
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--passes", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--out", type=Path, default=capstone_freeze.ROOT / "capstone" / "runs" / "direct")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="seconds per HTTP call")
    add_freeze_arguments(parser)
    return parser


def _ask_all(client: McpClient, questions: list[dict], freeze: dict, passes: int, partial: Path) -> None:
    schema = load_schema()
    client.initialize()
    tool = client.the_tool()
    argument = question_argument(tool)
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        for n in range(1, passes + 1):
            for question in questions:
                started = datetime.now(timezone.utc)
                clock = time.monotonic()
                result = client.call(tool["name"], {argument: question["text"]})
                wall_ms = round((time.monotonic() - clock) * 1000)
                record = {
                    "route": "direct",
                    "freeze": freeze["short"],
                    "question_id": question["id"],
                    "pass": n,
                    "answer_text": redact(answer_text(result)),
                    "raw_output": json.dumps(redact_json(result), ensure_ascii=False),
                    "tool_calls": 1,  # one MCP tool call; the data agent's own steps are not visible
                    "input_tokens": None,  # the endpoint returns no token counts
                    "output_tokens": None,
                    "wall_ms": wall_ms,
                    "started_utc": _utc(started),
                }
                validate_record(record, schema)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                print(f"{question['id']} pass {n}: {wall_ms / 1000:.1f} s")


def main(argv: list[str] | None = None, transport=None) -> int:
    """Run the passes. `transport` replaces the HTTP transport (tests pass a fake)."""
    args = _parser().parse_args(argv)
    try:
        freeze = load_verified_freeze(args)
        url = os.environ.get(ENV_URL, "").strip()
        if not url:
            raise InputError(f"{ENV_URL} is not set")
        questions = json.loads(args.questions.read_text(encoding="utf-8"))
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        asked, dropped = scored_questions(questions, expected)
        for qid in dropped:
            print(f"{qid} dropped: optional, with no expected answer")
        if transport is None:
            transport = HttpTransport(url, CliToken(), timeout=args.timeout)
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        final = args.out / f"direct-{freeze['short']}-{stamp}.jsonl"
        partial = final.with_name(final.name + ".partial")
        if final.exists() or partial.exists():
            raise InputError(f"--out: {final.name} already exists")
        _ask_all(McpClient(transport), asked, freeze, args.passes, partial)
        partial.replace(final)
        print(f"wrote {len(asked) * args.passes} records to {final}")
        return 0
    except FreezeBroken as broken:
        print(str(broken), file=sys.stderr)
        return 1
    except DataAgentError as error:
        print(f"--data-agent-dir: {error}", file=sys.stderr)
        return 2
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 2
    except McpError as error:
        print(str(error), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
