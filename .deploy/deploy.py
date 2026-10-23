"""Deploy script run by GitHub Actions after azure/login (OIDC).

Subcommands: upload-wheel, publish, ensure-shortcut, onelake-availability,
ensure-gateway-shortcut. Every input comes from environment variables so nothing
sensitive lands in argv or logs. Nothing here prints an environment value, a
token, a request body or a URL carrying the shortcut location or a query URI.

The real-time path (project 10) is gated on P10_EVENTHUB_CONNECTION_ID: while it is
unset or empty, publish leaves the real-time item types out and the two real-time
subcommands skip, so project 9's deploys keep working before the Eventstream's
connection exists and no Eventhouse idles on the capacity.

Not part of the `lakehouse` package. azure and fabric_cicd are imported lazily
inside the seam functions, so the tests need neither installed.
"""

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

ONELAKE_DFS_URL = "https://onelake.dfs.fabric.microsoft.com"
FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
# The Kusto audience for an Eventhouse's query service. Learn, Kusto "Authentication over HTTPS"
# (Fabric view) gets the token with `--resource "https://api.kusto.windows.net"`:
# https://learn.microsoft.com/en-us/kusto/api/rest/authentication?view=microsoft-fabric
KUSTO_SCOPE = "https://api.kusto.windows.net/.default"
# Resolved from the script location, never the working directory.
REPOSITORY_DIRECTORY = str(Path(__file__).resolve().parent.parent / "fabric")
REALTIME_ITEM_TYPES = ["Eventhouse", "KQLDatabase", "KQLQueryset", "KQLDashboard", "Reflex", "Eventstream"]
ITEM_TYPES_IN_SCOPE = ["Lakehouse", "Notebook", "DataPipeline", *REALTIME_ITEM_TYPES]
SHORTCUT_PATH = "Files"
SHORTCUT_NAME = "cost_export"
# The lakehouse is found by name, so a first prod deploy needs no ID that only exists after publish.
LAKEHOUSE_NAME = "lh_portfolio"

# R13: the real-time path runs only when the Eventstream's Fabric connection exists.
REALTIME_GATE = "P10_EVENTHUB_CONNECTION_ID"
# parameter.yml's $ENV: token needs this fabric-cicd 1.4.0 feature flag (fabric_cicd.append_feature_flag).
ENV_REPLACEMENT_FLAG = "enable_environment_variable_replacement"
# fabric-cicd 1.4.0 refuses the whole parameter file when an active $ENV: token's variable is not set
# (_parameter._validate_dynamic_replacement_variables), whatever the item scope. With the gate off the
# Eventstream is out of scope, so publish stands in git's own value, the empty GUID.
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"

# The gateway table in the Eventhouse, and its shortcut in lh_portfolio.
KQL_DATABASE_NAME = "kql_gateway"
GATEWAY_TABLE = "gateway_calls"
# R1: lh_portfolio is schema-enabled, so a table shortcut lives in a schema folder.
GATEWAY_SHORTCUT_PATH = "Tables/dbo"
GATEWAY_SHORTCUT_NAME = "gateway_calls"
# OneLake availability for one table, with the shortest write delay Learn allows (5 minutes to 3
# hours). The command text is Learn's own example, minus its trailing `;`:
# https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability
# https://learn.microsoft.com/en-us/kusto/management/alter-merge-mirroring-policy-command?view=microsoft-fabric
# alter-merge merges these properties into the table's policy, so a second run changes nothing.
MIRRORING_COMMAND = (
    f".alter-merge table {GATEWAY_TABLE} policy mirroring dataformat=parquet "
    "with (IsEnabled=true, TargetLatencyInMinutes=5)"
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
    """The error code of a Fabric (`errorCode`) or Kusto (`error.code`) error body, if it is safe to
    print; otherwise "unknown"."""
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


def _realtime_enabled():
    return bool(os.environ.get(REALTIME_GATE))


# --- seams (the tests replace these) ---------------------------------------


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


def _find_item(workspace_id, token, collection, name, kind):
    """The item named `name` in the workspace's `collection` list (for example `lakehouses`),
    following continuation tokens. StepFailed if the list call fails, no item has that name, or the
    listing does not end (a continuation token repeats, or MAX_LOOKUP_PAGES pages go by without the
    item). `kind` names the item in messages; nothing returned from the API is printed."""
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/{collection}"
    next_url = url
    seen_tokens = set()
    for _ in range(MAX_LOOKUP_PAGES):
        status, body = _http_get(next_url, token)  # status before body
        if not 200 <= status < 300 or not isinstance(body, dict):
            raise StepFailed(f"HTTP {status} {_error_code(body)}")
        for item in body.get("value") or []:
            if isinstance(item, dict) and item.get("displayName") == name and item.get("id"):
                return item
        token_value = body.get("continuationToken")
        if not token_value:
            raise StepFailed(f"{kind} {name} not found")
        if token_value in seen_tokens:
            break
        seen_tokens.add(token_value)
        next_url = f"{url}?continuationToken={quote(token_value, safe='')}"
    raise StepFailed(f"{kind} lookup did not finish")


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
    if _realtime_enabled():
        item_types = list(ITEM_TYPES_IN_SCOPE)
    else:
        item_types = [t for t in ITEM_TYPES_IN_SCOPE if t not in REALTIME_ITEM_TYPES]
        os.environ[REALTIME_GATE] = EMPTY_GUID  # see EMPTY_GUID; only this process sees it
        print(f"publish: real-time items skipped (no {REALTIME_GATE})")
    # fabric-cicd validates parameter.yml, $ENV: tokens included, when the workspace is built.
    _enable_feature_flag(ENV_REPLACEMENT_FLAG)
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
    if not _realtime_enabled():
        print(f"onelake-availability: skipped (no {REALTIME_GATE})")
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
    if not _realtime_enabled():
        print(f"ensure-gateway-shortcut: skipped (no {REALTIME_GATE})")
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


COMMANDS = {
    "upload-wheel": upload_wheel,
    "publish": publish,
    "ensure-shortcut": ensure_shortcut,
    "onelake-availability": onelake_availability,
    "ensure-gateway-shortcut": ensure_gateway_shortcut,
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
