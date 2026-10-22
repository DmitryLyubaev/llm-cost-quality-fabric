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

ONELAKE_DFS_URL = "https://onelake.dfs.fabric.microsoft.com"
FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
# Resolved from the script location, never the working directory.
REPOSITORY_DIRECTORY = str(Path(__file__).resolve().parent.parent / "fabric")
ITEM_TYPES_IN_SCOPE = ["Lakehouse", "Notebook", "DataPipeline"]
SHORTCUT_PATH = "Files"
SHORTCUT_NAME = "cost_export"

_ERROR_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class MissingEnvironment(Exception):
    def __init__(self, name):
        super().__init__(name)
        self.name = name


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


def _fabric_workspace(**kwargs):
    from fabric_cicd import FabricWorkspace

    return FabricWorkspace(**kwargs)


def _publish(workspace):
    from fabric_cicd import publish_all_items

    publish_all_items(workspace)


# --- subcommands -------------------------------------------------------------


def upload_wheel():
    workspace_id = _env("FABRIC_WORKSPACE_ID")
    lakehouse_id = _env("FABRIC_LAKEHOUSE_ID")
    wheel = Path(_env("WHEEL_PATH"))
    client = _datalake_client(_credential())
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
    lakehouse_id = _env("FABRIC_LAKEHOUSE_ID")
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
    url = (
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{lakehouse_id}/shortcuts"
        "?shortcutConflictPolicy=CreateOrOverwrite"
    )
    token = _credential().get_token(FABRIC_SCOPE).token
    status, response_body = _http_post(url, body, token)  # status before body
    if not 200 <= status < 300:
        code = response_body.get("errorCode") if isinstance(response_body, dict) else None
        if not (isinstance(code, str) and _ERROR_CODE.fullmatch(code)):
            code = "unknown"
        print(f"ensure-shortcut: HTTP {status} {code}", file=sys.stderr)
        return 1
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


if __name__ == "__main__":
    sys.exit(main())
