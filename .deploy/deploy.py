"""Deploy script run by GitHub Actions after azure/login (OIDC).

Subcommands: upload-wheel, publish, ensure-shortcut. Every input comes from
environment variables so nothing sensitive lands in argv or logs. Nothing here
prints an environment value, a token, a request body or a URL carrying the
shortcut location.

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
# Resolved from the script location, never the working directory.
REPOSITORY_DIRECTORY = str(Path(__file__).resolve().parent.parent / "fabric")
ITEM_TYPES_IN_SCOPE = ["Lakehouse", "Notebook", "DataPipeline"]
SHORTCUT_PATH = "Files"
SHORTCUT_NAME = "cost_export"
# The lakehouse is found by name, so a first prod deploy needs no ID that only exists after publish.
LAKEHOUSE_NAME = "lh_portfolio"

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
    code = body.get("errorCode") if isinstance(body, dict) else None
    return code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else "unknown"


def _env(name):
    value = os.environ.get(name)
    if not value:
        raise MissingEnvironment(name)
    return value


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
        url, json=json, headers={"Authorization": f"Bearer {token}"}, timeout=60
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


def _lakehouse_id(workspace_id, token):
    """The ID of the lakehouse named LAKEHOUSE_NAME in the workspace, following continuation
    tokens. StepFailed if the list call fails or no lakehouse has that name."""
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/lakehouses"
    next_url = url
    while next_url:
        status, body = _http_get(next_url, token)  # status before body
        if not 200 <= status < 300 or not isinstance(body, dict):
            raise StepFailed(f"HTTP {status} {_error_code(body)}")
        for item in body.get("value") or []:
            if isinstance(item, dict) and item.get("displayName") == LAKEHOUSE_NAME and item.get("id"):
                return str(item["id"])
        token_value = body.get("continuationToken")
        next_url = f"{url}?continuationToken={quote(token_value, safe='')}" if token_value else None
    raise StepFailed(f"lakehouse {LAKEHOUSE_NAME} not found")


def _fabric_workspace(**kwargs):
    from fabric_cicd import FabricWorkspace

    return FabricWorkspace(**kwargs)


def _publish(workspace):
    from fabric_cicd import publish_all_items

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
    workspace = _fabric_workspace(
        workspace_id=workspace_id,
        environment=environment,
        repository_directory=REPOSITORY_DIRECTORY,
        item_type_in_scope=list(ITEM_TYPES_IN_SCOPE),
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
        raise ValueError("COST_EXPORT_SUBPATH must start with '/'")
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


COMMANDS = {
    "upload-wheel": upload_wheel,
    "publish": publish,
    "ensure-shortcut": ensure_shortcut,
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


if __name__ == "__main__":
    sys.exit(main())
