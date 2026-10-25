"""Deploy script run by GitHub Actions after azure/login (OIDC).

Subcommands: upload-wheel, publish, ensure-shortcut, onelake-availability,
ensure-gateway-shortcut, refresh-model, and publish-capstone, which is never run from Actions:
the owner runs it by hand as fabric@, signed in through the Azure CLI, against the capstone
workspace. Every input comes from environment variables so
nothing sensitive lands in argv or logs. Nothing here prints an environment value, a
token, a request body or a URL carrying the shortcut location or a query URI.

The real-time path (project 10) is gated on P10_EVENTHUB_CONNECTION_ID: while it is
unset or empty, publish leaves the real-time item types out and the two real-time
subcommands skip, so project 9's deploys keep working before the Eventstream's
connection exists and no Eventhouse idles on the capacity.

The semantic model path (project 11) has its own, independent gate,
P11_MODEL_CONNECTION_ID: while it is unset or empty, publish leaves SemanticModel and
Report out and refresh-model skips.

Not part of the `lakehouse` package. azure and fabric_cicd are imported lazily
inside the seam functions, so the tests need neither installed.
"""

import argparse
import contextlib
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

ONELAKE_DFS_URL = "https://onelake.dfs.fabric.microsoft.com"
FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
# The Kusto audience for an Eventhouse's query service. Learn, Kusto "Authentication over HTTPS"
# (Fabric view) gets the token with `--resource "https://api.kusto.windows.net"`:
# https://learn.microsoft.com/en-us/kusto/api/rest/authentication?view=microsoft-fabric
KUSTO_SCOPE = "https://api.kusto.windows.net/.default"
# Learn documents the semantic model refresh as a Power BI REST call (refresh-dataset-in-group below).
# Learn's Power BI REST troubleshooting page: the token's audience must be
# https://analysis.windows.net/powerbi/api for the global cloud; Learn's service-principal embedding
# samples ask for it as this `.default` scope. A service principal needs no delegated scope (its rights
# are its workspace role plus the tenant setting that lets service principals call Power BI APIs):
# https://learn.microsoft.com/en-us/power-bi/developer/embedded/troubleshoot-rest-api
# https://learn.microsoft.com/en-us/power-bi/developer/embedded/embed-service-principal-certificate
POWERBI_API_BASE = "https://api.powerbi.com/v1.0/myorg"
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
# Resolved from the script location, never the working directory.
REPOSITORY_DIRECTORY = str(Path(__file__).resolve().parent.parent / "fabric")
REALTIME_ITEM_TYPES = ["Eventhouse", "KQLDatabase", "KQLQueryset", "KQLDashboard", "Reflex", "Eventstream"]
MODEL_ITEM_TYPES = ["SemanticModel", "Report"]
ITEM_TYPES_IN_SCOPE = ["Lakehouse", "Notebook", "DataPipeline", *REALTIME_ITEM_TYPES, *MODEL_ITEM_TYPES]
SHORTCUT_PATH = "Files"
SHORTCUT_NAME = "cost_export"
# The lakehouse is found by name, so a first prod deploy needs no ID that only exists after publish.
LAKEHOUSE_NAME = "lh_portfolio"
# Found by name too: it exists only after a publish with the model gate open.
SEMANTIC_MODEL_NAME = "sm_portfolio"

# The capstone (spec 4.2): its data agent, the snapshot lakehouse and the two notebooks, in their own
# folder with their own parameter.yml, deployed by publish-capstone to the capstone workspace and never
# by publish. No gate and no $ENV: token, so neither the feature flag nor a stand-in.
CAPSTONE_REPOSITORY_DIRECTORY = str(Path(__file__).resolve().parent.parent / "fabric-capstone")
CAPSTONE_ITEM_TYPES = ["Lakehouse", "Notebook", "DataAgent"]
CAPSTONE_ENVIRONMENT = "CAPSTONE"
# Found by name after fabric-cicd has created or updated it. List Data Agents (paged by continuationToken):
# https://learn.microsoft.com/en-us/rest/api/fabric/dataagent/items/list-data-agents
DATA_AGENT_NAME = "da_portfolio"
# Publish Data Agent promotes the staging (draft) configuration to the published one, which is the only
# one MCP and Foundry can reach. POST .../dataAgents/{id}/staging/publish with {publishedDescription};
# Learn documents 200 OK only (no long-running operation), and service principals and users alike:
# https://learn.microsoft.com/en-us/rest/api/fabric/dataagent/items/publish-data-agent
DATA_AGENT_DESCRIPTION = (
    "Answers questions about the portfolio's Azure spend, evaluation studies, live checks and API "
    "gateway calls, from a frozen snapshot of the gold tables in lh_capstone."
)

# R13: the real-time path runs only when the Eventstream's Fabric connection exists.
REALTIME_GATE = "P10_EVENTHUB_CONNECTION_ID"
# Project 11: the model and report deploy only when the model's Fabric connection exists. fabric-cicd
# binds the model to it after publish (parameter.yml's semantic_model_binding), so refresh-model does not.
MODEL_GATE = "P11_MODEL_CONNECTION_ID"
# What each closed gate leaves out of publish, and how publish names it when it skips them.
GATED_ITEM_TYPES = (
    (REALTIME_GATE, REALTIME_ITEM_TYPES, "real-time items"),
    (MODEL_GATE, MODEL_ITEM_TYPES, "model and report"),
)
# parameter.yml's $ENV: token needs this fabric-cicd 1.4.0 feature flag (fabric_cicd.append_feature_flag).
ENV_REPLACEMENT_FLAG = "enable_environment_variable_replacement"
# fabric-cicd 1.4.0 refuses the whole parameter file when an active $ENV: token's variable is not set
# (_parameter._validate_dynamic_replacement_variables), whatever the item scope; a semantic_model_binding
# connection_id left as the literal token fails its GUID check (_validate_connection_id) the same way.
# With a gate off its items are out of scope, so publish stands in git's own value, the empty GUID.
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"

# An enhanced refresh: any field besides notifyOption makes it one, and only an enhanced refresh can be
# read back by its ID. notifyOption does not apply to a service principal. A full refresh of a Direct Lake
# model reframes it on the lakehouse's current Delta versions; transactional keeps the old frame on failure.
# https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/refresh-dataset-in-group
# https://learn.microsoft.com/en-us/power-bi/connect-data/asynchronous-refresh
REFRESH_REQUEST = {"type": "Full", "commitMode": "transactional"}
# Get Refresh Execution Details: extendedStatus (else status) of the refresh; anything else (NotStarted,
# InProgress, Unknown) is still running.
# https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-execution-details-in-group
REFRESH_DONE = "Completed"
REFRESH_FAILED = ("Failed", "TimedOut", "Cancelled", "Disabled")
# Get Refresh History (`?$top=1`, the latest entry): status `Unknown` means "the completion state is
# unknown or a refresh is in progress". It lists every kind of refresh, not only enhanced ones.
# https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-history-in-group
HISTORY_RUNNING = "Unknown"
REFRESH_POLL_SECONDS = 15
# One cap for the whole step: waiting for an earlier refresh and for this one.
REFRESH_TIMEOUT_SECONDS = 600
# R7: the model must be bound to its fixed-identity connection, not left on SSO (`Automatic`).
# List Item Connections returns `value` entries with `id` and `connectivityType`, paged by
# continuationToken: https://learn.microsoft.com/en-us/rest/api/fabric/core/items/list-item-connections
BOUND_CONNECTIVITY = "ShareableCloud"
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# A URL does not end in `)`, `,` or `.`: those close the sentence around it, so they stay outside the match.
_URL = re.compile(r"https?://[^\s'\"<>]*[^\s'\"<>).,]")

# The gateway table in the Eventhouse, and its shortcut in lh_portfolio.
KQL_DATABASE_NAME = "kql_gateway"
GATEWAY_TABLE = "gateway_calls"
# R1: lh_portfolio is schema-enabled, so a table shortcut lives in a schema folder.
GATEWAY_SHORTCUT_PATH = "Tables/dbo"
GATEWAY_SHORTCUT_NAME = "gateway_calls"
# OneLake availability for one table, with the shortest write delay Learn allows (5 minutes to 3
# hours). Learn's OneLake-availability example (minus its trailing `;`) plus `Backfill=true` (default
# false), so rows ingested before a first or re-run step still reach OneLake; no cost on an empty table:
# https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability
# https://learn.microsoft.com/en-us/kusto/management/alter-merge-mirroring-policy-command?view=microsoft-fabric
# alter-merge merges these properties into the table's policy, so a second run changes nothing.
MIRRORING_COMMAND = (
    f".alter-merge table {GATEWAY_TABLE} policy mirroring dataformat=parquet "
    "with (IsEnabled=true, TargetLatencyInMinutes=5, Backfill=true)"
)
# Management commands are a POST to /v1/rest/mgmt with a JSON body {db, csl}:
# https://learn.microsoft.com/en-us/kusto/api/rest/request?view=microsoft-fabric
KUSTO_MGMT_PATH = "/v1/rest/mgmt"

# A lookup that is still paging after this many pages, or is handed a continuation token it has
# already used, is not going to finish.
MAX_LOOKUP_PAGES = 100

_ERROR_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class MissingEnvironment(Exception):
    def __init__(self, name):
        super().__init__(name)
        self.name = name


class StepFailed(Exception):
    """A step failed; `reason` is safe to print (no IDs, URLs, tokens or bodies)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _error_code(body):
    """The error code of a Fabric (`errorCode`) or Kusto or Power BI (`error.code`) error body, if it
    is safe to print; otherwise "unknown"."""
    code = None
    if isinstance(body, dict):
        code = body.get("errorCode")
        error = body.get("error")
        if code is None and isinstance(error, dict):
            code = error.get("code")
    return code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else "unknown"


def _env(name):
    value = os.environ.get(name)
    if not value:
        raise MissingEnvironment(name)
    return value


def _gate_open(gate):
    """A gate is open when its connection variable is set and not empty (Actions passes a missing
    secret as an empty string)."""
    return bool(os.environ.get(gate))


def _skipped(command, gate):
    """True, after printing the skip line, when `command` must not run because `gate` is closed."""
    if _gate_open(gate):
        return False
    print(f"{command}: skipped (no {gate})")
    return True


@contextlib.contextmanager
def _stand_ins(gates):
    """Set each of `gates` to EMPTY_GUID for the block (see EMPTY_GUID), then put back what was there:
    the previous value, or no variable at all. Only this process sees it."""
    saved = {gate: os.environ.get(gate) for gate in gates}
    os.environ.update({gate: EMPTY_GUID for gate in gates})
    try:
        yield
    finally:
        for gate, value in saved.items():
            if value is None:
                os.environ.pop(gate, None)
            else:
                os.environ[gate] = value


def _redact(text):
    """`text` with every URL as <url> and every GUID as <id>."""
    return _GUID.sub("<id>", _URL.sub("<url>", text))


class _RedactingFilter(logging.Filter):
    """Rewrites a log record's rendered message, traceback and stack text through _redact. Lets every
    record through."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:  # a record whose arguments do not fit its format
            message = str(record.msg)
        record.msg, record.args = _redact(message), None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = _redact(record.exc_text)  # formatters use the cached text
        if record.stack_info:
            record.stack_info = _redact(record.stack_info)
        return True


@contextlib.contextmanager
def _redacted_fabric_cicd_logs():
    """fabric-cicd 1.4.0 logs item IDs and request URLs at INFO, its default level, for example
    `Binding semantic model '...' (ID: <model id>) to connection '...'` and `{e!s}` on a failed bind
    (_items/_semanticmodel.py). For the block, a _RedactingFilter sits on every `fabric_cicd` logger
    (the records it creates) and on every handler those records reach: the `fabric_cicd` logger's
    console handler, the `console_only` logger's, and the root logger's (its log file), so a logger
    created later is covered too. Removed afterwards, whatever happens."""
    redactor = _RedactingFilter()
    names = {"fabric_cicd", "console_only"} | {
        name for name in logging.Logger.manager.loggerDict if name.startswith("fabric_cicd.")
    }
    loggers = [logging.getLogger(name) for name in sorted(names)]
    targets = loggers + [h for lg in [*loggers, logging.getLogger()] for h in lg.handlers]
    targets = list({id(t): t for t in targets}.values())
    for target in targets:
        target.addFilter(redactor)
    try:
        yield
    finally:
        for target in targets:
            target.removeFilter(redactor)


# --- seams (the tests replace these) ---------------------------------------


_sleep = time.sleep
_monotonic = time.monotonic


def _credential():
    from azure.identity import AzureCliCredential

    return AzureCliCredential()


def _datalake_client(credential):
    from azure.storage.filedatalake import DataLakeServiceClient

    return DataLakeServiceClient(account_url=ONELAKE_DFS_URL, credential=credential)


def _http_post(url, json, token):
    """POST json; return (status, parsed JSON body or {}). Never logs anything."""
    import requests

    response = requests.post(
        url,
        json=json,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=60,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body


def _http_get(url, token):
    """GET; return (status, parsed JSON body or {}). Never logs anything."""
    import requests

    response = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body


def _http_post_location(url, json, token):
    """POST json; return (status, parsed JSON body or {}, the Location header or None). For an
    asynchronous start whose answer is in the header. Never logs anything."""
    import requests

    response = requests.post(
        url,
        json=json,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=60,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body, response.headers.get("Location")


def _paged(url, token, kind):
    """Every entry (a dict) of a paged Fabric list, following continuation tokens. StepFailed if a
    page call fails or the listing does not end (a continuation token repeats, or MAX_LOOKUP_PAGES
    pages go by). `kind` names the list in messages; nothing returned from the API is printed."""
    next_url = url
    seen_tokens = set()
    for _ in range(MAX_LOOKUP_PAGES):
        status, body = _http_get(next_url, token)  # status before body
        if not 200 <= status < 300 or not isinstance(body, dict):
            raise StepFailed(f"HTTP {status} {_error_code(body)}")
        entries = body.get("value")
        yield from (e for e in entries if isinstance(e, dict)) if isinstance(entries, list) else ()
        token_value = body.get("continuationToken")
        if not token_value:
            return
        if token_value in seen_tokens:
            break
        seen_tokens.add(token_value)
        next_url = f"{url}?continuationToken={quote(token_value, safe='')}"
    raise StepFailed(f"{kind} lookup did not finish")


def _find_item(workspace_id, token, collection, name, kind):
    """The item named `name` in the workspace's `collection` list (for example `lakehouses`), read
    with _paged. StepFailed if the list call fails, no item has that name, or the listing does not
    end. `kind` names the item in messages; nothing returned from the API is printed."""
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/{collection}"
    for item in _paged(url, token, kind):
        if item.get("displayName") == name and item.get("id"):
            return item
    raise StepFailed(f"{kind} {name} not found")


def _lakehouse_id(workspace_id, token):
    """The ID of the lakehouse named LAKEHOUSE_NAME in the workspace (see _find_item)."""
    return str(_find_item(workspace_id, token, "lakehouses", LAKEHOUSE_NAME, "lakehouse")["id"])


def _kql_database(workspace_id, token):
    """The KQL database named KQL_DATABASE_NAME, as the list call returns it (see _find_item)."""
    return _find_item(workspace_id, token, "kqlDatabases", KQL_DATABASE_NAME, "kql database")


def _query_service_uri(workspace_id, database, token):
    """The database's query URI (`properties.queryServiceUri`), from the list entry or else from a
    GET of the item. Never a hard-coded host: it differs per Eventhouse."""
    properties = database.get("properties")
    uri = properties.get("queryServiceUri") if isinstance(properties, dict) else None
    if not uri:
        url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/kqlDatabases/{database['id']}"
        status, body = _http_get(url, token)  # status before body
        if not 200 <= status < 300 or not isinstance(body, dict):
            raise StepFailed(f"HTTP {status} {_error_code(body)}")
        properties = body.get("properties")
        uri = properties.get("queryServiceUri") if isinstance(properties, dict) else None
    if not isinstance(uri, str) or not uri.startswith("https://"):
        raise StepFailed(f"kql database {KQL_DATABASE_NAME} has no query URI")
    return uri.rstrip("/")


def _enable_feature_flag(name):
    from fabric_cicd import append_feature_flag

    append_feature_flag(name)


def _load_fabric_cicd():
    """Import fabric_cicd, which sets up its loggers and their console handler on import, so the
    redaction filter has those handlers to sit on (see _redacted_fabric_cicd_logs)."""
    import fabric_cicd  # noqa: F401


def _fabric_workspace(**kwargs):
    from fabric_cicd import FabricWorkspace

    return FabricWorkspace(**kwargs)


def _publish(workspace):
    from fabric_cicd import publish_all_items

    # No exclude_path for the KQL database nested in eh_gateway's `.children`: fabric-cicd 1.4.0's
    # EventhousePublisher applies its own (constants.EXCLUDE_PATH_REGEX_MAPPING), and
    # publish_all_items takes no such argument.
    publish_all_items(workspace)


# --- subcommands -------------------------------------------------------------


def upload_wheel():
    workspace_id = _env("FABRIC_WORKSPACE_ID")
    wheel = Path(_env("WHEEL_PATH"))
    credential = _credential()
    # A Fabric-scoped token for the lookup; the DataLake client asks the same credential for a
    # storage-scoped one itself.
    lakehouse_id = _lakehouse_id(workspace_id, credential.get_token(FABRIC_SCOPE).token)
    client = _datalake_client(credential)
    file_client = client.get_file_system_client(workspace_id).get_file_client(
        f"{lakehouse_id}/Files/wheels/{wheel.name}"
    )
    file_client.upload_data(wheel.read_bytes(), overwrite=True)
    print(f"upload-wheel: uploaded {wheel.name}")
    return 0


def publish():
    workspace_id = _env("FABRIC_WORKSPACE_ID")
    environment = _env("FABRIC_ENVIRONMENT")
    item_types = list(ITEM_TYPES_IN_SCOPE)
    closed = []
    for gate, gated_types, label in GATED_ITEM_TYPES:  # independent gates
        if _gate_open(gate):
            continue
        item_types = [t for t in item_types if t not in gated_types]
        closed.append(gate)
        print(f"publish: {label} skipped (no {gate})")
    _enable_feature_flag(ENV_REPLACEMENT_FLAG)  # imports fabric_cicd, which sets up its loggers
    # Never raise fabric-cicd to debug (change_log_level): it logs substituted $ENV values there. At
    # its default INFO level it still logs item IDs and request URLs (the model's ID when it binds
    # it), so its log lines are redacted while it runs; the connection ID, a secret, is masked by
    # Actions as well.
    with _redacted_fabric_cicd_logs():
        # fabric-cicd reads and validates parameter.yml, $ENV: tokens included, only while the
        # workspace is built (FabricWorkspace.__init__ -> _refresh_parameter_file), so a closed gate's
        # stand-in is needed only for that.
        with _stand_ins(closed):
            workspace = _fabric_workspace(
                workspace_id=workspace_id,
                environment=environment,
                repository_directory=REPOSITORY_DIRECTORY,
                item_type_in_scope=item_types,
                token_credential=_credential(),
            )
        _publish(workspace)  # no unpublish: orphans are left alone
    print("publish: done")
    return 0


def ensure_shortcut():
    workspace_id = _env("FABRIC_WORKSPACE_ID")
    connection_id = _env("FABRIC_CONNECTION_ID")
    location = _env("COST_EXPORT_LOCATION")
    subpath = _env("COST_EXPORT_SUBPATH")
    if not subpath.startswith("/"):
        raise StepFailed("COST_EXPORT_SUBPATH must start with '/'")
    body = {
        "path": SHORTCUT_PATH,
        "name": SHORTCUT_NAME,
        # adlsGen2: the storage account has hierarchical namespace on.
        "target": {
            "adlsGen2": {
                "location": location,
                "subpath": subpath,
                "connectionId": connection_id,
            }
        },
    }
    token = _credential().get_token(FABRIC_SCOPE).token
    lakehouse_id = _lakehouse_id(workspace_id, token)
    url = (
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{lakehouse_id}/shortcuts"
        "?shortcutConflictPolicy=CreateOrOverwrite"
    )
    status, response_body = _http_post(url, body, token)  # status before body
    if not 200 <= status < 300:
        raise StepFailed(f"HTTP {status} {_error_code(response_body)}")
    print("ensure-shortcut: ok")
    return 0


def onelake_availability():
    """Expose gateway_calls in OneLake (Delta) so lh_portfolio can shortcut to it."""
    if _skipped("onelake-availability", REALTIME_GATE):
        return 0
    workspace_id = _env("FABRIC_WORKSPACE_ID")
    credential = _credential()
    fabric_token = credential.get_token(FABRIC_SCOPE).token
    database = _kql_database(workspace_id, fabric_token)
    query_uri = _query_service_uri(workspace_id, database, fabric_token)
    kusto_token = credential.get_token(KUSTO_SCOPE).token
    body = {"db": KQL_DATABASE_NAME, "csl": MIRRORING_COMMAND}
    status, response_body = _http_post(f"{query_uri}{KUSTO_MGMT_PATH}", body, kusto_token)
    if not 200 <= status < 300:  # status before body
        raise StepFailed(f"HTTP {status} {_error_code(response_body)}")
    print("onelake-availability: ok")
    return 0


def ensure_gateway_shortcut():
    """A OneLake shortcut in lh_portfolio to the gateway table that onelake-availability exposes.
    The shortcut API takes a KQL database as a OneLake target item:
    https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts/create-shortcut"""
    if _skipped("ensure-gateway-shortcut", REALTIME_GATE):
        return 0
    workspace_id = _env("FABRIC_WORKSPACE_ID")
    token = _credential().get_token(FABRIC_SCOPE).token
    lakehouse_id = _lakehouse_id(workspace_id, token)
    database_id = str(_kql_database(workspace_id, token)["id"])
    body = {
        "path": GATEWAY_SHORTCUT_PATH,
        "name": GATEWAY_SHORTCUT_NAME,
        "target": {
            "oneLake": {
                "workspaceId": workspace_id,
                "itemId": database_id,
                "path": f"Tables/{GATEWAY_TABLE}",
            }
        },
    }
    url = (
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{lakehouse_id}/shortcuts"
        "?shortcutConflictPolicy=CreateOrOverwrite"
    )
    status, response_body = _http_post(url, body, token)  # status before body
    if not 200 <= status < 300:
        raise StepFailed(f"HTTP {status} {_error_code(response_body)}")
    print("ensure-gateway-shortcut: ok")
    return 0


def _poll(url, token, finished, deadline, what):
    """GET `url` every REFRESH_POLL_SECONDS until `finished(body)` returns a state (returned), or
    StepFailed once `deadline` (a _monotonic time) has passed, naming `what` did not finish. Sleeps
    through the _sleep / _monotonic seams; nothing returned from the API is printed."""
    while True:
        status, body = _http_get(url, token)  # status before body
        if not 200 <= status < 300 or not isinstance(body, dict):
            raise StepFailed(f"HTTP {status} {_error_code(body)}")
        state = finished(body)
        if state is not None:
            return state
        if _monotonic() >= deadline:
            # The refresh may still finish in the service; a re-run starts a new one once it has.
            raise StepFailed(f"{what} did not finish in {REFRESH_TIMEOUT_SECONDS // 60} minutes")
        _sleep(REFRESH_POLL_SECONDS)


def _refresh_outcome(body):
    """A Get Refresh Execution Details body's final state (Learn's names), or None while it runs
    (202 with NotStarted / InProgress / Unknown, or a state outside Learn's list, never printed)."""
    state = body.get("extendedStatus") or body.get("status")
    return state if state == REFRESH_DONE or state in REFRESH_FAILED else None


def _history_idle(body):
    """'idle' unless the latest Get Refresh History entry is still running (status Unknown)."""
    entries = body.get("value")
    latest = entries[0] if isinstance(entries, list) and entries else None
    running = isinstance(latest, dict) and latest.get("status") == HISTORY_RUNNING
    return None if running else "idle"


def _wait_for_refresh(url, token, deadline):
    """Poll Get Refresh Execution Details at `url` until the refresh completes (returns) or fails
    (StepFailed with Learn's status name), or `deadline` passes."""
    state = _poll(url, token, _refresh_outcome, deadline, "refresh")
    if state != REFRESH_DONE:
        raise StepFailed(f"refresh {state}")


def _semantic_model_id(workspace_id, token):
    """The ID of the semantic model named SEMANTIC_MODEL_NAME (see _find_item). List Semantic Models:
    https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/list-semantic-models"""
    return str(_find_item(workspace_id, token, "semanticModels", SEMANTIC_MODEL_NAME, "semantic model")["id"])


def _require_binding(workspace_id, model_id, connection_id, token):
    """R7: fabric-cicd's bind is best-effort (skipped or only warned about when it cannot bind), so
    check the result: one of the model's connections (List Item Connections, paged) must be
    `connection_id` with connectivityType ShareableCloud. An SSO (`Automatic`) entry has no id.
    StepFailed otherwise, naming no ID."""
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{model_id}/connections"
    for entry in _paged(url, token, "model connection"):
        entry_id = entry.get("id")
        if (
            isinstance(entry_id, str)
            and entry_id.lower() == connection_id.lower()
            and entry.get("connectivityType") == BOUND_CONNECTIVITY
        ):
            return
    raise StepFailed("the model is not bound to its connection")


def refresh_model():
    """Refresh (reframe) sm_portfolio after the deploy: fabric-cicd publishes the definition but
    sends no refresh, and a Direct Lake table stays unprocessed, failing queries, until one runs.
    The connection binding is fabric-cicd's (parameter.yml's semantic_model_binding, after publish);
    this step only checks it (_require_binding) and binds nothing. The deploy identity owns the model
    it published, as binding requires ("The caller must be the owner of the semantic model"):
    https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/bind-semantic-model-connection"""
    if _skipped("refresh-model", MODEL_GATE):
        return 0
    workspace_id = _env("FABRIC_WORKSPACE_ID")
    connection_id = _env(MODEL_GATE)  # open gate: the connection the model must be bound to
    credential = _credential()
    fabric_token = credential.get_token(FABRIC_SCOPE).token
    model_id = _semantic_model_id(workspace_id, fabric_token)
    _require_binding(workspace_id, model_id, connection_id, fabric_token)
    # A semantic model's Fabric item ID is its Power BI dataset ID.
    token = credential.get_token(POWERBI_SCOPE).token
    refreshes = f"{POWERBI_API_BASE}/groups/{workspace_id}/datasets/{model_id}/refreshes"
    deadline = _monotonic() + REFRESH_TIMEOUT_SECONDS
    # The service takes one refresh at a time per model and answers a second with 400 (Learn's
    # enhanced-refresh page), so wait for one already running, an automatic one included, to end.
    # How it ended does not matter: this refresh replaces it.
    _poll(f"{refreshes}?$top=1", token, _history_idle, deadline, "an earlier refresh")
    status, body, location = _http_post_location(refreshes, REFRESH_REQUEST, token)
    if not 200 <= status < 300:  # status before body; Learn documents 202 Accepted
        raise StepFailed(f"HTTP {status} {_error_code(body)}")
    # The refresh ID is the Location header's last segment. Only the ID is used: the status URL is
    # built here, so the token never goes to a host the response names.
    refresh_id = (location or "").rstrip("/").rsplit("/", 1)[-1]
    if not _GUID.fullmatch(refresh_id):
        raise StepFailed("the refresh has no ID")
    _wait_for_refresh(f"{refreshes}/{refresh_id}", token, deadline)
    print("refresh-model: ok")
    return 0


def _data_agent_id(workspace_id, token):
    """The ID of the data agent named DATA_AGENT_NAME (see _find_item)."""
    return str(_find_item(workspace_id, token, "dataAgents", DATA_AGENT_NAME, "data agent")["id"])


def publish_capstone():
    """Deploy fabric-capstone/ (lh_capstone, its notebooks, da_portfolio) to the capstone workspace,
    then publish da_portfolio: fabric-cicd deploys the definition, whose draft stage is all it writes,
    and an unpublished data agent cannot be reached over MCP or from Foundry. Run by hand as fabric@
    (the AzureCliCredential of _credential); a re-run deploys and publishes again."""
    workspace_id = _env("FABRIC_WORKSPACE_ID")
    credential = _credential()
    _load_fabric_cicd()  # its loggers and handlers exist before the redaction goes on them
    # The same redaction as publish: fabric-cicd logs item IDs and request URLs at INFO.
    with _redacted_fabric_cicd_logs():
        workspace = _fabric_workspace(
            workspace_id=workspace_id,
            environment=CAPSTONE_ENVIRONMENT,
            repository_directory=CAPSTONE_REPOSITORY_DIRECTORY,  # reads fabric-capstone/parameter.yml
            item_type_in_scope=list(CAPSTONE_ITEM_TYPES),
            token_credential=credential,
        )
        _publish(workspace)  # no unpublish: orphans are left alone
    token = credential.get_token(FABRIC_SCOPE).token
    agent_id = _data_agent_id(workspace_id, token)
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/dataAgents/{agent_id}/staging/publish"
    status, body = _http_post(url, {"publishedDescription": DATA_AGENT_DESCRIPTION}, token)
    # Status before body. Only 200 is documented: anything else, a 202 included, is a publish this
    # step did not see finish.
    if status != 200:
        raise StepFailed(f"HTTP {status} {_error_code(body)}")
    print("publish-capstone: ok")
    return 0


COMMANDS = {
    "upload-wheel": upload_wheel,
    "publish": publish,
    "ensure-shortcut": ensure_shortcut,
    "onelake-availability": onelake_availability,
    "ensure-gateway-shortcut": ensure_gateway_shortcut,
    "refresh-model": refresh_model,
    "publish-capstone": publish_capstone,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deploy the lakehouse to Fabric")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command]()
    except MissingEnvironment as missing:
        print(f"missing environment variable {missing.name}", file=sys.stderr)
        return 2
    except StepFailed as failed:
        print(f"{args.command}: {failed.reason}", file=sys.stderr)
        return 1
    except Exception as err:
        # Only the type: an exception's text can carry a URL, an ID or a token, and a traceback
        # carries all of them.
        print(f"{args.command}: failed ({type(err).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
