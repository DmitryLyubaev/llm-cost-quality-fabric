"""Tests for .deploy/deploy.py. Every Azure/Fabric call sits behind a seam the
tests replace; nothing here touches the network."""

import importlib.util
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

DEPLOY_PY = Path(__file__).resolve().parent.parent / ".deploy" / "deploy.py"

WS = "11111111-aaaa-bbbb-cccc-000000000001"
LH = "22222222-aaaa-bbbb-cccc-000000000002"
CONN = "33333333-aaaa-bbbb-cccc-000000000003"
LOCATION = "https://secretacct.dfs.core.windows.net"
SUBPATH = "/exports/costs"
OTHER_LH = "44444444-aaaa-bbbb-cccc-000000000004"
LIST_URL = f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/lakehouses"
KQL = "55555555-aaaa-bbbb-cccc-000000000005"
OTHER_KQL = "66666666-aaaa-bbbb-cccc-000000000006"
EVENTHUB_CONNECTION = "77777777-aaaa-bbbb-cccc-000000000007"
# Not a Kusto host: the repo-wide hygiene check rejects a named `*.kusto.fabric...` host.
QUERY_URI = "https://qs-secret-host.example.test"
KQL_LIST_URL = f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/kqlDatabases"
KQL_ITEM_URL = f"{KQL_LIST_URL}/{KQL}"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
KUSTO_SCOPE = "https://api.kusto.windows.net/.default"
MIRRORING_COMMAND = (
    ".alter-merge table gateway_calls policy mirroring dataformat=parquet "
    "with (IsEnabled=true, TargetLatencyInMinutes=5, Backfill=true)"
)
REALTIME_TYPES = ["Eventhouse", "KQLDatabase", "KQLQueryset", "KQLDashboard", "Reflex", "Eventstream"]
SKIP_REASON = "(no P10_EVENTHUB_CONNECTION_ID)"
# Project 11: the semantic model and its refresh, behind P11_MODEL_CONNECTION_ID.
MODEL = "88888888-aaaa-bbbb-cccc-000000000008"
OTHER_MODEL = "99999999-aaaa-bbbb-cccc-000000000009"
MODEL_CONNECTION = "aaaaaaaa-aaaa-bbbb-cccc-00000000000a"
REFRESH = "bbbbbbbb-aaaa-bbbb-cccc-00000000000b"
MODEL_LIST_URL = f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/semanticModels"
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
REFRESHES_URL = f"https://api.powerbi.com/v1.0/myorg/groups/{WS}/datasets/{MODEL}/refreshes"
REFRESH_URL = f"{REFRESHES_URL}/{REFRESH}"
MODEL_TYPES = ["SemanticModel", "Report"]
MODEL_SKIP_REASON = "(no P11_MODEL_CONNECTION_ID)"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
CONNECTIONS_URL = f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/items/{MODEL}/connections"
HISTORY_URL = f"{REFRESHES_URL}?$top=1"
EARLIER_REFRESH = "cccccccc-aaaa-bbbb-cccc-00000000000c"
OTHER_CONNECTION = "dddddddd-aaaa-bbbb-cccc-00000000000d"
NOT_BOUND = "refresh-model: the model is not bound to its connection"


@pytest.fixture(autouse=True)
def no_realtime_gate(monkeypatch):
    """Every test starts with P10_EVENTHUB_CONNECTION_ID unset, and whatever publish writes to it is
    undone afterwards (setenv first, so monkeypatch records the variable and restores its absence)."""
    monkeypatch.setenv("P10_EVENTHUB_CONNECTION_ID", "unset-by-fixture")
    monkeypatch.delenv("P10_EVENTHUB_CONNECTION_ID")


@pytest.fixture(autouse=True)
def no_model_gate(monkeypatch):
    """The same for project 11's gate, P11_MODEL_CONNECTION_ID."""
    monkeypatch.setenv("P11_MODEL_CONNECTION_ID", "unset-by-fixture")
    monkeypatch.delenv("P11_MODEL_CONNECTION_ID")


@pytest.fixture
def deploy():
    spec = importlib.util.spec_from_file_location("deploy_script", DEPLOY_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCredential:
    def __init__(self):
        self.calls = []

    def get_token(self, *scopes):
        self.scopes = scopes
        self.calls.append(scopes)
        # One token per audience, so a test can tell which one reached which call.
        tokens = {(KUSTO_SCOPE,): "kusto-tok-secret", (POWERBI_SCOPE,): "pbi-tok-secret"}
        token = tokens.get(scopes, "tok-secret-value")
        return SimpleNamespace(token=token)


class FakeFileClient:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def upload_data(self, data, overwrite=False):
        self.store["path"] = self.path
        self.store["data"] = data
        self.store["overwrite"] = overwrite


class FakeFileSystem:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def get_file_client(self, path):
        self.store["file_system"] = self.name
        return FakeFileClient(self.store, path)


class FakeDatalake:
    def __init__(self, store):
        self.store = store

    def get_file_system_client(self, file_system):
        return FakeFileSystem(self.store, file_system)


@pytest.fixture
def env(monkeypatch, tmp_path):
    wheel = tmp_path / "lakehouse-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    values = {
        "FABRIC_WORKSPACE_ID": WS,
        "FABRIC_CONNECTION_ID": CONN,
        "FABRIC_ENVIRONMENT": "PROD",
        "COST_EXPORT_LOCATION": LOCATION,
        "COST_EXPORT_SUBPATH": SUBPATH,
        "WHEEL_PATH": str(wheel),
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    return values


@pytest.fixture
def seams(deploy, monkeypatch):
    cred = FakeCredential()
    monkeypatch.setattr(deploy, "_credential", lambda: cred)
    state = SimpleNamespace(cred=cred, upload={}, posts=[], response=(201, {}),
                            ws_kwargs=None, published=[], gets=[], pages=[(200, {"value": [
                                {"displayName": "lh_other", "id": OTHER_LH},
                                {"displayName": "lh_portfolio", "id": LH},
                            ]})], routes=None, events=[], env_at_build=None,
                            model_env_at_build=None, location_response=(202, {}, REFRESH_URL),
                            clock=0.0, sleeps=[])

    def datalake_client(credential):
        assert credential is cred
        return FakeDatalake(state.upload)

    def http_post(url, json, token):
        state.posts.append((url, json, token))
        return state.response

    def http_post_location(url, json, token):
        state.posts.append((url, json, token))
        return state.location_response

    def http_get(url, token):
        state.gets.append((url, token))
        if state.routes is not None:
            route = state.routes[url]
            if isinstance(route, list):  # successive responses; the last one repeats
                return route.pop(0) if len(route) > 1 else route[0]
            return route
        return state.pages[len(state.gets) - 1]

    def sleep(seconds):
        state.sleeps.append(seconds)
        state.clock += seconds

    def fabric_workspace(**kwargs):
        state.events.append("workspace")
        state.env_at_build = os.environ.get("P10_EVENTHUB_CONNECTION_ID")
        state.model_env_at_build = os.environ.get("P11_MODEL_CONNECTION_ID")
        state.ws_kwargs = kwargs
        return "WS-OBJECT"

    monkeypatch.setattr(deploy, "_datalake_client", datalake_client)
    monkeypatch.setattr(deploy, "_http_post", http_post)
    monkeypatch.setattr(deploy, "_http_get", http_get)
    monkeypatch.setattr(deploy, "_http_post_location", http_post_location)
    monkeypatch.setattr(deploy, "_sleep", sleep)
    monkeypatch.setattr(deploy, "_monotonic", lambda: state.clock)
    monkeypatch.setattr(deploy, "_fabric_workspace", fabric_workspace)
    monkeypatch.setattr(deploy, "_publish", state.published.append)
    monkeypatch.setattr(deploy, "_enable_feature_flag", lambda name: state.events.append(("flag", name)))
    return state


def kql_list(*items, token=None):
    body = {"value": list(items)}
    if token:
        body["continuationToken"] = token
    return 200, body


def kql_item(name="kql_gateway", item_id=KQL, uri=QUERY_URI):
    item = {"displayName": name, "id": item_id, "type": "KQLDatabase"}
    if uri is not None:
        item["properties"] = {"queryServiceUri": uri, "databaseType": "ReadWrite"}
    return item


@pytest.fixture
def realtime(monkeypatch, seams):
    """The real-time path is on (R13) and the workspace holds lh_portfolio and kql_gateway."""
    # setenv before any delenv in a test, so monkeypatch restores the variable's absence afterwards.
    monkeypatch.setenv("P10_EVENTHUB_CONNECTION_ID", EVENTHUB_CONNECTION)
    seams.routes = {
        LIST_URL: (200, {"value": [{"displayName": "lh_portfolio", "id": LH}]}),
        KQL_LIST_URL: kql_list(kql_item("kql_other", OTHER_KQL), kql_item()),
    }
    seams.response = (200, {})
    return seams


def model_list(*items, token=None):
    body = {"value": list(items)}
    if token:
        body["continuationToken"] = token
    return 200, body


def refresh_state(extended, status=None):
    """A Get Refresh Execution Details response: 202 while running, 200 when finished."""
    running = extended in ("NotStarted", "InProgress", "Unknown")
    body = {"status": status or ("Unknown" if running else extended), "extendedStatus": extended}
    return (202 if running else 200), body


def connection(connection_id=MODEL_CONNECTION, connectivity="ShareableCloud"):
    """A List Item Connections entry; an SSO (Automatic) entry has no id."""
    entry = {
        "connectivityType": connectivity,
        "connectionDetails": {"type": "AzureDataLakeStorage", "path": f"https://onelake.example.test/{WS}/{LH}/"},
    }
    if connection_id is not None:
        entry.update(id=connection_id, displayName="conn_portfolio")
    return entry


def history(*statuses):
    """A Get Refresh History response ($top=1): the latest entry with that status, or no entry."""
    return 200, {"value": [{"requestId": EARLIER_REFRESH, "refreshType": "ViaApi", "status": s} for s in statuses]}


def refresh_polls(state):
    """The status GETs refresh-model made for its own refresh (not the history)."""
    return [url for url, _ in state.gets if url.startswith(f"{REFRESHES_URL}/")]


@pytest.fixture
def model(monkeypatch, seams):
    """The model path is on (P11), the workspace holds sm_portfolio bound to its connection, no
    other refresh is running, and its refresh runs once, then completes. Ask for it after `realtime`
    when a test needs both: it adds to the routes."""
    # setenv before any delenv in a test, so monkeypatch restores the variable's absence afterwards.
    monkeypatch.setenv("P11_MODEL_CONNECTION_ID", MODEL_CONNECTION)
    seams.routes = {
        **(seams.routes or {}),
        MODEL_LIST_URL: model_list(
            {"displayName": "sm_other", "id": OTHER_MODEL}, {"displayName": "sm_portfolio", "id": MODEL}
        ),
        CONNECTIONS_URL: (200, {"value": [connection(None, "Automatic"), connection()]}),
        HISTORY_URL: history("Completed"),
        REFRESH_URL: [refresh_state("InProgress"), refresh_state("Completed")],
    }
    return seams


def test_upload_wheel_path(deploy, env, seams):
    assert deploy.main(["upload-wheel"]) == 0
    assert seams.upload["file_system"] == WS
    assert seams.upload["path"] == f"{LH}/Files/wheels/lakehouse-0.1.0-py3-none-any.whl"
    assert seams.upload["data"] == b"wheel-bytes"
    assert seams.upload["overwrite"] is True
    assert deploy.ONELAKE_DFS_URL == "https://onelake.dfs.fabric.microsoft.com"
    # the lakehouse is looked up by name with a Fabric-scoped token from the same credential
    assert seams.gets == [(LIST_URL, "tok-secret-value")]
    assert seams.cred.scopes == ("https://api.fabric.microsoft.com/.default",)


def test_the_lakehouse_id_is_not_an_input(deploy, env, seams, monkeypatch):
    monkeypatch.setenv("FABRIC_LAKEHOUSE_ID", OTHER_LH)
    for command in ("upload-wheel", "ensure-shortcut"):
        assert deploy.main([command]) == 0
        seams.gets.clear()
    assert seams.upload["path"].startswith(f"{LH}/")
    assert f"/items/{LH}/" in seams.posts[0][0]
    assert "FABRIC_LAKEHOUSE_ID" not in DEPLOY_PY.read_text(encoding="utf-8")


def test_lakehouse_lookup_follows_continuation_tokens(deploy, seams):
    seams.pages = [
        (200, {"value": [{"displayName": "lh_other", "id": OTHER_LH}], "continuationToken": "a b/c"}),
        (200, {"value": [{"displayName": "lh_portfolio", "id": LH}]}),
    ]
    assert deploy._lakehouse_id(WS, "tok") == LH
    assert [url for url, _ in seams.gets] == [LIST_URL, f"{LIST_URL}?continuationToken=a%20b%2Fc"]


def test_lakehouse_lookup_stops_after_100_pages(deploy, seams, monkeypatch):
    """A server that never stops paging (every token new) cannot hold the deploy forever."""
    calls = []

    def endless(url, token):
        calls.append(url)
        assert len(calls) <= 200, "the lookup never stopped paging"
        return 200, {"value": [{"displayName": "lh_other", "id": OTHER_LH}], "continuationToken": f"t{len(calls)}"}

    monkeypatch.setattr(deploy, "_http_get", endless)

    with pytest.raises(deploy.StepFailed, match="lakehouse lookup did not finish") as failed:
        deploy._lakehouse_id(WS, "tok")

    assert failed.value.reason == "lakehouse lookup did not finish"
    assert len(calls) == 100


def test_the_lakehouse_can_be_on_the_last_allowed_page(deploy, seams, monkeypatch):
    calls = []

    def paged(url, token):
        calls.append(url)
        if len(calls) == 100:
            return 200, {"value": [{"displayName": "lh_portfolio", "id": LH}]}
        return 200, {"value": [], "continuationToken": f"t{len(calls)}"}

    monkeypatch.setattr(deploy, "_http_get", paged)

    assert deploy._lakehouse_id(WS, "tok") == LH
    assert len(calls) == 100


def test_lakehouse_lookup_stops_when_a_continuation_token_repeats(deploy, seams):
    seams.pages = [
        (200, {"value": [], "continuationToken": "same"}),
        (200, {"value": [], "continuationToken": "same"}),
        (200, {"value": [{"displayName": "lh_portfolio", "id": LH}]}),  # never reached
    ]

    with pytest.raises(deploy.StepFailed, match="lakehouse lookup did not finish"):
        deploy._lakehouse_id(WS, "tok")

    assert len(seams.gets) == 2


@pytest.mark.parametrize("command", ["upload-wheel", "ensure-shortcut"])
def test_missing_lakehouse_exits_1_without_ids(deploy, env, seams, capsys, command):
    seams.pages = [
        (200, {"value": [{"displayName": "lh_other", "id": OTHER_LH}], "continuationToken": "next"}),
        (200, {"value": []}),
    ]
    assert deploy.main([command]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == f"{command}: lakehouse lh_portfolio not found"
    assert out == ""
    assert seams.upload == {} and seams.posts == []
    for value in (WS, LH, OTHER_LH, "tok-secret-value"):
        assert value not in out + err


def test_lakehouse_lookup_http_error_prints_status_and_code_only(deploy, env, seams, capsys):
    seams.pages = [(401, {"errorCode": "TokenExpired", "message": f"workspace {WS}"})]
    assert deploy.main(["upload-wheel"]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == "upload-wheel: HTTP 401 TokenExpired"
    assert out == "" and WS not in err
    assert seams.upload == {}


def test_publish_scope_and_args(deploy, env, seams):
    # env holds no P10_EVENTHUB_CONNECTION_ID: the project 9 scope only (R13)
    assert deploy.main(["publish"]) == 0
    assert seams.ws_kwargs == {
        "workspace_id": WS,
        "environment": "PROD",
        "repository_directory": str(DEPLOY_PY.parent.parent / "fabric"),
        "item_type_in_scope": ["Lakehouse", "Notebook", "DataPipeline"],
        "token_credential": seams.cred,
    }
    assert Path(seams.ws_kwargs["repository_directory"]).is_absolute()
    assert seams.published == ["WS-OBJECT"]


def test_ensure_shortcut_payload(deploy, env, seams):
    assert deploy.main(["ensure-shortcut"]) == 0
    (url, body, token), = seams.posts
    assert url.startswith(f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/items/{LH}/shortcuts?")
    assert body == {
        "path": "Files",
        "name": "cost_export",
        "target": {"adlsGen2": {"location": LOCATION, "subpath": SUBPATH,
                                "connectionId": CONN}},
    }
    assert token == "tok-secret-value"
    assert seams.cred.scopes == ("https://api.fabric.microsoft.com/.default",)


def test_ensure_shortcut_is_idempotent_policy(deploy, env, seams):
    deploy.main(["ensure-shortcut"])
    url = seams.posts[0][0]
    assert url.endswith("?shortcutConflictPolicy=CreateOrOverwrite")


def test_ensure_shortcut_subpath_must_start_with_slash(deploy, env, seams, monkeypatch, capsys):
    monkeypatch.setenv("COST_EXPORT_SUBPATH", "exports/costs")
    assert deploy.main(["ensure-shortcut"]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == "ensure-shortcut: COST_EXPORT_SUBPATH must start with '/'"
    assert out == "" and "exports/costs" not in err
    assert seams.posts == []


def test_an_unexpected_error_in_publish_prints_only_its_type(
    deploy, env, realtime, model, monkeypatch, capsys
):
    # both gates on (realtime, model): nothing on stdout; the gate-off case is the next test
    url = f"https://x/workspaces/{WS}/items"

    def failing_publish(workspace):
        raise RuntimeError(f"publish to {url} failed")

    monkeypatch.setattr(deploy, "_publish", failing_publish)

    assert deploy.main(["publish"]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == "publish: failed (RuntimeError)"
    assert out == ""
    for value in (url, WS, "https://x", "Traceback", EVENTHUB_CONNECTION, MODEL_CONNECTION):
        assert value not in out + err


def test_an_unexpected_error_in_publish_with_the_gate_off(deploy, env, seams, monkeypatch, capsys):
    url = f"https://x/workspaces/{WS}/items"

    def failing_publish(workspace):
        raise RuntimeError(f"publish to {url} failed")

    monkeypatch.setattr(deploy, "_publish", failing_publish)

    assert deploy.main(["publish"]) == 1
    out, err = capsys.readouterr()
    assert out == (
        "publish: real-time items skipped (no P10_EVENTHUB_CONNECTION_ID)\n"
        "publish: model and report skipped (no P11_MODEL_CONNECTION_ID)\n"
    )
    assert err == "publish: failed (RuntimeError)\n"
    for value in (url, WS, "https://x", "Traceback", "00000000-0000-0000-0000-000000000000"):
        assert value not in out + err


@pytest.mark.parametrize(
    "command",
    ["upload-wheel", "ensure-shortcut", "onelake-availability", "ensure-gateway-shortcut", "refresh-model"],
)
def test_an_unexpected_error_prints_only_its_type(
    deploy, env, realtime, model, monkeypatch, capsys, command
):
    """No message, URL, workspace ID or traceback: a ConnectionError text carries the request URL."""
    url = f"https://x/workspaces/{WS}/lakehouses"

    def failing_get(requested, token):
        raise ConnectionError(url)

    monkeypatch.setattr(deploy, "_http_get", failing_get)

    assert deploy.main([command]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == f"{command}: failed (ConnectionError)"
    assert out == ""
    for value in (
        url, WS, "https://x", "Traceback", "tok-secret-value", EVENTHUB_CONNECTION, MODEL_CONNECTION,
    ):
        assert value not in out + err
    assert realtime.upload == {} and realtime.posts == []


@pytest.mark.parametrize("body, code", [
    ({"errorCode": "InsufficientPrivileges", "message": f"bad {LOCATION}"}, "InsufficientPrivileges"),
    ({"message": "no code here"}, "unknown"),
    ("not-json-object", "unknown"),
    ({"errorCode": f"{LOCATION} injected"}, "unknown"),
])
def test_non_2xx_prints_status_not_body(deploy, env, seams, capsys, body, code):
    seams.response = (403, body)
    assert deploy.main(["ensure-shortcut"]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == f"ensure-shortcut: HTTP 403 {code}"
    assert out == ""
    for value in (LOCATION, CONN, WS, LH, SUBPATH, "tok-secret-value", "no code here"):
        assert value not in out + err


def test_missing_env_names_variable_only(deploy, env, seams, monkeypatch, capsys):
    monkeypatch.delenv("FABRIC_CONNECTION_ID")
    assert deploy.main(["ensure-shortcut"]) == 2
    out, err = capsys.readouterr()
    assert err.strip() == "missing environment variable FABRIC_CONNECTION_ID"
    assert out == ""
    assert seams.posts == []


@pytest.mark.parametrize("command, name", [
    ("upload-wheel", "WHEEL_PATH"),
    ("publish", "FABRIC_ENVIRONMENT"),
    ("ensure-shortcut", "COST_EXPORT_LOCATION"),
    ("onelake-availability", "FABRIC_WORKSPACE_ID"),
    ("ensure-gateway-shortcut", "FABRIC_WORKSPACE_ID"),
    ("refresh-model", "FABRIC_WORKSPACE_ID"),
])
def test_missing_env_per_command(deploy, env, realtime, model, monkeypatch, capsys, command, name):
    monkeypatch.delenv(name)
    assert deploy.main([command]) == 2
    out, err = capsys.readouterr()
    assert err.strip() == f"missing environment variable {name}"
    assert EVENTHUB_CONNECTION not in out + err and MODEL_CONNECTION not in out + err
    assert realtime.gets == [] and realtime.posts == []


ALL_COMMANDS = (
    "upload-wheel", "publish", "ensure-shortcut", "onelake-availability", "ensure-gateway-shortcut",
    "refresh-model",
)


def test_no_secret_in_output(deploy, env, realtime, model, capsys):
    for command in ALL_COMMANDS:
        assert deploy.main([command]) == 0, command
    realtime.response = (500, {"errorCode": "Boom", "message": LOCATION})
    realtime.location_response = (500, {"error": {"code": "Boom", "message": f"{WS}/{MODEL}"}}, None)
    for command in ("ensure-shortcut", "onelake-availability", "ensure-gateway-shortcut", "refresh-model"):
        assert deploy.main([command]) == 1, command
    out, err = capsys.readouterr()
    for value in list(env.values()) + [
        "tok-secret-value", "kusto-tok-secret", "pbi-tok-secret", LH, OTHER_LH, KQL, OTHER_KQL,
        EVENTHUB_CONNECTION, QUERY_URI, "qs-secret-host", MODEL, OTHER_MODEL, MODEL_CONNECTION, REFRESH,
    ]:
        assert value not in out + err


# --- R12 / R13: the real-time items and their gate ----------------------------


def test_publish_turns_on_environment_variable_replacement_before_building_the_workspace(
    deploy, env, seams
):
    """parameter.yml's $ENV: token needs the flag; fabric-cicd validates the parameter file when the
    FabricWorkspace is constructed, so the flag must already be set by then (Task 5 note M-4)."""
    assert deploy.main(["publish"]) == 0
    assert seams.events == [("flag", "enable_environment_variable_replacement"), "workspace"]


@pytest.mark.parametrize("value", [None, ""])
def test_publish_without_the_connection_leaves_the_realtime_items_out(
    deploy, env, seams, monkeypatch, capsys, value
):
    monkeypatch.setenv("P10_EVENTHUB_CONNECTION_ID", "x")
    if value is None:
        monkeypatch.delenv("P10_EVENTHUB_CONNECTION_ID")
    else:
        monkeypatch.setenv("P10_EVENTHUB_CONNECTION_ID", value)

    assert deploy.main(["publish"]) == 0

    assert seams.ws_kwargs["item_type_in_scope"] == ["Lakehouse", "Notebook", "DataPipeline"]
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        f"publish: real-time items skipped {SKIP_REASON}",
        f"publish: model and report skipped {MODEL_SKIP_REASON}",
        "publish: done",
    ]
    assert err == ""
    # fabric-cicd 1.4.0 rejects the parameter file when an active $ENV: token's variable is unset,
    # whatever the item scope, so publish stands in the empty GUID (git's own value) for the build.
    assert seams.env_at_build == "00000000-0000-0000-0000-000000000000"
    assert seams.published == ["WS-OBJECT"]


def test_publish_with_the_connection_includes_the_realtime_items(deploy, env, realtime, capsys):
    assert deploy.main(["publish"]) == 0

    assert realtime.ws_kwargs["item_type_in_scope"] == [
        "Lakehouse", "Notebook", "DataPipeline", *REALTIME_TYPES,
    ]
    assert realtime.env_at_build == EVENTHUB_CONNECTION  # left as it is
    out, err = capsys.readouterr()
    # the P11 gate is off here; the line for it is project 11's (see the gate tests below)
    assert out.splitlines() == [f"publish: model and report skipped {MODEL_SKIP_REASON}", "publish: done"]
    assert EVENTHUB_CONNECTION not in out + err


def test_publish_passes_no_exclude_path_for_the_nested_kql_database(deploy, env, realtime):
    """M-3: fabric-cicd 1.4.0's EventhousePublisher applies its own `.children` exclude
    (constants.EXCLUDE_PATH_REGEX_MAPPING), and publish_all_items takes no exclude_path."""
    published = []
    deploy._publish = lambda workspace, **kwargs: published.append((workspace, kwargs))
    assert deploy.main(["publish"]) == 0
    assert published == [("WS-OBJECT", {})]


@pytest.mark.parametrize("command", ["onelake-availability", "ensure-gateway-shortcut"])
@pytest.mark.parametrize("value", [None, ""])
def test_realtime_commands_skip_without_the_connection(
    deploy, env, seams, monkeypatch, capsys, command, value
):
    monkeypatch.setenv("P10_EVENTHUB_CONNECTION_ID", "x")
    if value is None:
        monkeypatch.delenv("P10_EVENTHUB_CONNECTION_ID")
    else:
        monkeypatch.setenv("P10_EVENTHUB_CONNECTION_ID", value)
    # the skip comes first: not even the workspace ID is needed
    monkeypatch.delenv("FABRIC_WORKSPACE_ID")

    assert deploy.main([command]) == 0

    out, err = capsys.readouterr()
    assert out.strip() == f"{command}: skipped {SKIP_REASON}"
    assert err == ""
    assert seams.gets == [] and seams.posts == [] and seams.cred.calls == []


# --- onelake-availability -------------------------------------------------------


def test_onelake_availability_runs_the_mirroring_policy_on_the_looked_up_database(
    deploy, env, realtime, capsys
):
    assert deploy.main(["onelake-availability"]) == 0

    # the database is found by name with a Fabric token; the command goes to its query URI with a
    # Kusto token
    assert realtime.gets == [(KQL_LIST_URL, "tok-secret-value")]
    (url, body, token), = realtime.posts
    assert url == f"{QUERY_URI}/v1/rest/mgmt"
    assert body == {"db": "kql_gateway", "csl": MIRRORING_COMMAND}
    assert token == "kusto-tok-secret"
    assert realtime.cred.calls == [(FABRIC_SCOPE,), (KUSTO_SCOPE,)]
    out, err = capsys.readouterr()
    assert out.strip() == "onelake-availability: ok"
    assert err == ""


def test_onelake_availability_is_idempotent(deploy, env, realtime):
    assert deploy.main(["onelake-availability"]) == 0
    assert deploy.main(["onelake-availability"]) == 0
    first, second = realtime.posts
    assert first == second
    # alter-merge: running it again merges the same properties into the existing policy
    assert first[1]["csl"].startswith(".alter-merge table gateway_calls policy mirroring")


def test_onelake_availability_gets_the_item_when_the_list_has_no_query_uri(deploy, env, realtime):
    realtime.routes[KQL_LIST_URL] = kql_list(kql_item(uri=None))
    realtime.routes[KQL_ITEM_URL] = (200, kql_item(uri=QUERY_URI + "/"))

    assert deploy.main(["onelake-availability"]) == 0

    assert [url for url, _ in realtime.gets] == [KQL_LIST_URL, KQL_ITEM_URL]
    assert realtime.posts[0][0] == f"{QUERY_URI}/v1/rest/mgmt"  # no double slash


@pytest.mark.parametrize("uri", [None, "", "http://plain-text.example.test", 42])
def test_onelake_availability_without_an_https_query_uri_fails(deploy, env, realtime, capsys, uri):
    item = kql_item(uri=None)
    if uri is not None:
        item["properties"] = {"queryServiceUri": uri}
    realtime.routes[KQL_LIST_URL] = kql_list(item)
    realtime.routes[KQL_ITEM_URL] = (200, item)

    assert deploy.main(["onelake-availability"]) == 1

    out, err = capsys.readouterr()
    assert err.strip() == "onelake-availability: kql database kql_gateway has no query URI"
    assert out == "" and realtime.posts == []


@pytest.mark.parametrize("body, code", [
    ({"error": {"code": "BadRequest_EntityNotFound", "message": f"table at {QUERY_URI}"}},
     "BadRequest_EntityNotFound"),
    ({"error": {"code": f"{QUERY_URI} injected"}}, "unknown"),
    ({"error": "flat"}, "unknown"),
    ({"errorCode": "Forbidden"}, "Forbidden"),
])
def test_onelake_availability_non_2xx_prints_status_and_code_only(
    deploy, env, realtime, capsys, body, code
):
    realtime.response = (400, body)
    assert deploy.main(["onelake-availability"]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == f"onelake-availability: HTTP 400 {code}"
    assert out == ""
    for value in (QUERY_URI, "qs-secret-host", KQL, WS, "kusto-tok-secret"):
        assert value not in out + err


def test_onelake_availability_item_get_error_prints_status_and_code_only(deploy, env, realtime, capsys):
    realtime.routes[KQL_LIST_URL] = kql_list(kql_item(uri=None))
    realtime.routes[KQL_ITEM_URL] = (404, {"errorCode": "ItemNotFound", "message": KQL})
    assert deploy.main(["onelake-availability"]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == "onelake-availability: HTTP 404 ItemNotFound"
    assert KQL not in out + err and realtime.posts == []


def test_the_deploy_script_names_no_kusto_host():
    text = DEPLOY_PY.read_text(encoding="utf-8")
    assert "kusto.fabric.microsoft.com" not in text
    assert "help.kusto" not in text


# --- the KQL database lookup shares the lakehouse lookup's rules -------------------


def test_kql_lookup_follows_continuation_tokens(deploy, seams):
    seams.pages = [
        kql_list(kql_item("kql_other", OTHER_KQL), token="a b/c"),
        kql_list(kql_item()),
    ]
    item = deploy._kql_database(WS, "tok")
    assert item["id"] == KQL
    assert [url for url, _ in seams.gets] == [KQL_LIST_URL, f"{KQL_LIST_URL}?continuationToken=a%20b%2Fc"]


def test_kql_lookup_stops_after_100_pages(deploy, seams, monkeypatch):
    calls = []

    def endless(url, token):
        calls.append(url)
        assert len(calls) <= 200, "the lookup never stopped paging"
        return kql_list(kql_item("kql_other", OTHER_KQL), token=f"t{len(calls)}")

    monkeypatch.setattr(deploy, "_http_get", endless)

    with pytest.raises(deploy.StepFailed) as failed:
        deploy._kql_database(WS, "tok")
    assert failed.value.reason == "kql database lookup did not finish"
    assert len(calls) == 100


def test_kql_lookup_stops_when_a_continuation_token_repeats(deploy, seams):
    seams.pages = [kql_list(token="same"), kql_list(token="same"), kql_list(kql_item())]
    with pytest.raises(deploy.StepFailed, match="kql database lookup did not finish"):
        deploy._kql_database(WS, "tok")
    assert len(seams.gets) == 2


@pytest.mark.parametrize("command", ["onelake-availability", "ensure-gateway-shortcut"])
def test_missing_kql_database_exits_1_without_ids(deploy, env, realtime, capsys, command):
    realtime.routes[KQL_LIST_URL] = kql_list(kql_item("kql_other", OTHER_KQL))
    assert deploy.main([command]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == f"{command}: kql database kql_gateway not found"
    assert out == "" and realtime.posts == []
    for value in (WS, LH, OTHER_KQL, QUERY_URI, "tok-secret-value", EVENTHUB_CONNECTION):
        assert value not in out + err


def test_kql_lookup_http_error_prints_status_and_code_only(deploy, env, realtime, capsys):
    realtime.routes[KQL_LIST_URL] = (403, {"errorCode": "InsufficientPrivileges", "message": WS})
    assert deploy.main(["onelake-availability"]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == "onelake-availability: HTTP 403 InsufficientPrivileges"
    assert WS not in out + err and realtime.posts == []


# --- ensure-gateway-shortcut -------------------------------------------------------


def test_ensure_gateway_shortcut_payload(deploy, env, realtime, capsys):
    assert deploy.main(["ensure-gateway-shortcut"]) == 0

    (url, body, token), = realtime.posts
    assert url == (
        f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/items/{LH}/shortcuts"
        "?shortcutConflictPolicy=CreateOrOverwrite"
    )
    # R1: lh_portfolio is schema-enabled, so the table shortcut sits in the dbo schema folder
    assert body == {
        "path": "Tables/dbo",
        "name": "gateway_calls",
        "target": {"oneLake": {"workspaceId": WS, "itemId": KQL, "path": "Tables/gateway_calls"}},
    }
    assert token == "tok-secret-value"
    assert realtime.cred.calls == [(FABRIC_SCOPE,)]
    assert {url for url, _ in realtime.gets} == {LIST_URL, KQL_LIST_URL}
    out, err = capsys.readouterr()
    assert out.strip() == "ensure-gateway-shortcut: ok"
    assert err == ""


def test_ensure_gateway_shortcut_is_idempotent(deploy, env, realtime):
    assert deploy.main(["ensure-gateway-shortcut"]) == 0
    assert deploy.main(["ensure-gateway-shortcut"]) == 0
    first, second = realtime.posts
    assert first == second
    assert first[0].endswith("?shortcutConflictPolicy=CreateOrOverwrite")


def test_ensure_gateway_shortcut_missing_lakehouse(deploy, env, realtime, capsys):
    realtime.routes[LIST_URL] = (200, {"value": [{"displayName": "lh_other", "id": OTHER_LH}]})
    assert deploy.main(["ensure-gateway-shortcut"]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == "ensure-gateway-shortcut: lakehouse lh_portfolio not found"
    assert out == "" and realtime.posts == []


def test_ensure_gateway_shortcut_non_2xx_prints_status_and_code_only(deploy, env, realtime, capsys):
    realtime.response = (409, {"errorCode": "ShortcutConflict", "message": f"{WS}/{LH}/{KQL}"})
    assert deploy.main(["ensure-gateway-shortcut"]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == "ensure-gateway-shortcut: HTTP 409 ShortcutConflict"
    for value in (WS, LH, KQL, "tok-secret-value"):
        assert value not in out + err


# --- P11: the semantic model, its gate and its refresh --------------------------------------------


@pytest.mark.parametrize("realtime_value", [None, EVENTHUB_CONNECTION])
@pytest.mark.parametrize("model_value", [None, "", MODEL_CONNECTION])
def test_publish_scope_follows_two_independent_gates(
    deploy, env, seams, monkeypatch, capsys, realtime_value, model_value
):
    """Review Focus 5: without P11_MODEL_CONNECTION_ID (unset, or empty as Actions passes a missing
    secret) the model and report stay out, and projects 9 and 10 publish exactly as before."""
    for name, value in (("P10_EVENTHUB_CONNECTION_ID", realtime_value), ("P11_MODEL_CONNECTION_ID", model_value)):
        if value is not None:
            monkeypatch.setenv(name, value)

    assert deploy.main(["publish"]) == 0

    expected = ["Lakehouse", "Notebook", "DataPipeline"]
    lines = []
    if realtime_value:
        expected += REALTIME_TYPES
    else:
        lines.append(f"publish: real-time items skipped {SKIP_REASON}")
    if model_value:
        expected += MODEL_TYPES
    else:
        lines.append(f"publish: model and report skipped {MODEL_SKIP_REASON}")
    assert seams.ws_kwargs["item_type_in_scope"] == expected
    out, err = capsys.readouterr()
    assert out.splitlines() == lines + ["publish: done"]
    assert err == ""
    # fabric-cicd 1.4.0 refuses parameter.yml when a $ENV: token is left unresolved (the
    # semantic_model_binding connection_id must then be a GUID), so a closed gate stands in the empty
    # GUID for the build; an open gate's value is passed through as it is.
    assert seams.env_at_build == (realtime_value or EMPTY_GUID)
    assert seams.model_env_at_build == (model_value or EMPTY_GUID)
    assert seams.events == [("flag", "enable_environment_variable_replacement"), "workspace"]
    assert seams.published == ["WS-OBJECT"]
    for value in (WS, EVENTHUB_CONNECTION, MODEL_CONNECTION, EMPTY_GUID):
        assert value not in out + err


def test_all_item_types_are_in_scope_with_both_gates_open(deploy, env, realtime, model):
    assert deploy.main(["publish"]) == 0
    assert realtime.ws_kwargs["item_type_in_scope"] == [
        "Lakehouse", "Notebook", "DataPipeline", *REALTIME_TYPES, *MODEL_TYPES,
    ]
    assert deploy.ITEM_TYPES_IN_SCOPE == realtime.ws_kwargs["item_type_in_scope"]
    assert deploy.MODEL_ITEM_TYPES == MODEL_TYPES


@pytest.mark.parametrize("value", [None, ""])
def test_refresh_model_skips_without_the_model_connection(deploy, env, realtime, monkeypatch, capsys, value):
    """Review Focus 5: the skip comes first, so not even the workspace ID is needed, and the real-time
    gate being open changes nothing."""
    if value is not None:
        monkeypatch.setenv("P11_MODEL_CONNECTION_ID", value)
    monkeypatch.delenv("FABRIC_WORKSPACE_ID")

    assert deploy.main(["refresh-model"]) == 0

    out, err = capsys.readouterr()
    assert out == f"refresh-model: skipped {MODEL_SKIP_REASON}\n"
    assert err == ""
    assert realtime.gets == [] and realtime.posts == [] and realtime.cred.calls == []


@pytest.mark.parametrize("command", ["onelake-availability", "ensure-gateway-shortcut"])
def test_the_realtime_commands_ignore_the_model_gate(deploy, env, seams, model, capsys, command):
    """The gates are independent: P11 open does not open project 10's steps."""
    assert deploy.main([command]) == 0
    out, _ = capsys.readouterr()
    assert out == f"{command}: skipped {SKIP_REASON}\n"
    assert seams.gets == [] and seams.posts == []


def test_refresh_model_starts_an_enhanced_refresh_and_waits_for_it(deploy, env, model, capsys):
    """The realtime gate is closed here: the model path does not need it."""
    assert deploy.main(["refresh-model"]) == 0

    # sm_portfolio is found by name and its binding checked with a Fabric token; the refresh goes to
    # the Power BI API with a Power BI token (the semantic model's item ID is its dataset ID), after
    # the history shows no other refresh running.
    assert model.gets == [
        (MODEL_LIST_URL, "tok-secret-value"),
        (CONNECTIONS_URL, "tok-secret-value"),
        (HISTORY_URL, "pbi-tok-secret"),
        (REFRESH_URL, "pbi-tok-secret"),
        (REFRESH_URL, "pbi-tok-secret"),
    ]
    (url, body, token), = model.posts
    assert url == REFRESHES_URL
    # Any field besides notifyOption makes it an enhanced refresh, the kind that can be polled by ID;
    # notifyOption itself does not apply to a service principal.
    assert body == {"type": "Full", "commitMode": "transactional"}
    assert token == "pbi-tok-secret"
    assert model.cred.calls == [(FABRIC_SCOPE,), (POWERBI_SCOPE,)]
    assert model.sleeps == [deploy.REFRESH_POLL_SECONDS]
    out, err = capsys.readouterr()
    assert out == "refresh-model: ok\n"
    assert err == ""


def test_refresh_model_binds_nothing_itself(deploy, env, model):
    """The binding path chosen: fabric-cicd 1.4.0 replaces $ENV: tokens in the whole parameter file
    before loading it, so its semantic_model_binding section binds the model during publish (see
    parameter.yml and VERIFY.md). refresh-model only checks the binding (GET) and refreshes: its one
    POST is the refresh."""
    assert deploy.main(["refresh-model"]) == 0
    assert [url for url, _, _ in model.posts] == [REFRESHES_URL]


def test_refresh_model_polls_its_own_url_not_the_location_host(deploy, env, model):
    """Only the refresh ID is taken from the Location header; the token never goes to its host."""
    model.location_response = (202, {}, f"https://elsewhere.example.test/refreshes/{REFRESH}")
    assert deploy.main(["refresh-model"]) == 0
    assert set(refresh_polls(model)) == {REFRESH_URL}
    assert not any("elsewhere" in url for url, _ in model.gets)


@pytest.mark.parametrize("location", [None, "", f"{REFRESHES_URL}/", f"{REFRESHES_URL}/not-a-guid"])
def test_refresh_model_without_a_refresh_id_fails(deploy, env, model, capsys, location):
    model.location_response = (202, {}, location)
    assert deploy.main(["refresh-model"]) == 1
    out, err = capsys.readouterr()
    assert err == "refresh-model: the refresh has no ID\n"
    assert out == "" and refresh_polls(model) == []


def test_refresh_model_waits_through_every_running_state(deploy, env, model, capsys):
    model.routes[REFRESH_URL] = [
        refresh_state("NotStarted"),
        refresh_state("Unknown"),
        refresh_state("InProgress"),
        refresh_state("Completed"),
    ]
    assert deploy.main(["refresh-model"]) == 0
    assert len(refresh_polls(model)) == 4
    assert model.sleeps == [deploy.REFRESH_POLL_SECONDS] * 3
    assert capsys.readouterr().out == "refresh-model: ok\n"


def test_refresh_model_reads_status_when_there_is_no_extended_status(deploy, env, model):
    model.routes[REFRESH_URL] = [(202, {"status": "Unknown"}), (200, {"status": "Completed"})]
    assert deploy.main(["refresh-model"]) == 0


@pytest.mark.parametrize("state", ["Failed", "TimedOut", "Cancelled", "Disabled"])
def test_refresh_model_fails_with_the_refresh_status(deploy, env, model, capsys, state):
    http, body = refresh_state(state, status="Disabled" if state == "Disabled" else "Failed")
    body["serviceExceptionJson"] = '{"errorCode":"x","workspace":"' + WS + '"}'
    body["messages"] = [{"message": f"model {MODEL} at {LOCATION}", "type": "Error"}]
    model.routes[REFRESH_URL] = [refresh_state("InProgress"), (http, body)]

    assert deploy.main(["refresh-model"]) == 1

    out, err = capsys.readouterr()
    assert out == ""
    assert err == f"refresh-model: refresh {state}\n"
    for value in (WS, MODEL, LOCATION, REFRESH, "pbi-tok-secret"):
        assert value not in out + err


def test_refresh_model_stops_waiting_after_10_minutes(deploy, env, model, capsys):
    model.routes[REFRESH_URL] = [refresh_state("InProgress")]

    assert deploy.main(["refresh-model"]) == 1

    out, err = capsys.readouterr()
    assert err == "refresh-model: refresh did not finish in 10 minutes\n"
    assert out == ""
    assert deploy.REFRESH_TIMEOUT_SECONDS == 600
    assert sum(model.sleeps) == 600 and model.clock == 600
    assert len(refresh_polls(model)) == 600 // deploy.REFRESH_POLL_SECONDS + 1  # one last look at the deadline


def test_refresh_model_never_prints_an_unknown_status(deploy, env, model, capsys):
    """A status outside Learn's list is treated as still running, and never printed."""
    model.routes[REFRESH_URL] = [(202, {"extendedStatus": f"Odd {WS}"})]
    assert deploy.main(["refresh-model"]) == 1
    out, err = capsys.readouterr()
    assert err == "refresh-model: refresh did not finish in 10 minutes\n"
    assert WS not in out + err


@pytest.mark.parametrize("body, code", [
    ({"error": {"code": "InvalidRequest", "message": f"model {MODEL} is refreshing"}}, "InvalidRequest"),
    ({"error": {"code": f"{WS} injected"}}, "unknown"),
    ({"errorCode": "Unauthorized"}, "Unauthorized"),
    ("not-json-object", "unknown"),
])
def test_refresh_model_start_error_prints_status_and_code_only(deploy, env, model, capsys, body, code):
    model.location_response = (400, body, REFRESH_URL)
    assert deploy.main(["refresh-model"]) == 1
    out, err = capsys.readouterr()
    assert err == f"refresh-model: HTTP 400 {code}\n"
    assert out == "" and refresh_polls(model) == []
    for value in (WS, MODEL, REFRESH, "pbi-tok-secret"):
        assert value not in out + err


def test_refresh_model_poll_error_prints_status_and_code_only(deploy, env, model, capsys):
    model.routes[REFRESH_URL] = [(404, {"error": {"code": "ItemNotFound", "message": f"{WS}/{MODEL}"}})]
    assert deploy.main(["refresh-model"]) == 1
    out, err = capsys.readouterr()
    assert err == "refresh-model: HTTP 404 ItemNotFound\n"
    assert WS not in out + err and MODEL not in out + err


def test_missing_model_exits_1_without_ids(deploy, env, model, capsys):
    model.routes[MODEL_LIST_URL] = model_list({"displayName": "sm_other", "id": OTHER_MODEL})
    assert deploy.main(["refresh-model"]) == 1
    out, err = capsys.readouterr()
    assert err == "refresh-model: semantic model sm_portfolio not found\n"
    assert out == "" and model.posts == []
    for value in (WS, OTHER_MODEL, MODEL_CONNECTION, "tok-secret-value"):
        assert value not in out + err


def test_model_lookup_follows_continuation_tokens(deploy, seams):
    seams.pages = [
        model_list({"displayName": "sm_other", "id": OTHER_MODEL}, token="a b/c"),
        model_list({"displayName": "sm_portfolio", "id": MODEL}),
    ]
    assert deploy._semantic_model_id(WS, "tok") == MODEL
    assert [url for url, _ in seams.gets] == [MODEL_LIST_URL, f"{MODEL_LIST_URL}?continuationToken=a%20b%2Fc"]


def test_model_lookup_stops_when_a_continuation_token_repeats(deploy, seams):
    seams.pages = [model_list(token="same"), model_list(token="same")]
    with pytest.raises(deploy.StepFailed, match="semantic model lookup did not finish"):
        deploy._semantic_model_id(WS, "tok")
    assert len(seams.gets) == 2


def test_model_lookup_http_error_prints_status_and_code_only(deploy, env, model, capsys):
    model.routes[MODEL_LIST_URL] = (403, {"errorCode": "InsufficientPrivileges", "message": WS})
    assert deploy.main(["refresh-model"]) == 1
    out, err = capsys.readouterr()
    assert err == "refresh-model: HTTP 403 InsufficientPrivileges\n"
    assert WS not in out + err and model.posts == []


def test_the_refresh_is_polled_without_really_sleeping(deploy):
    """The seams are the real clock and sleep, so the tests replace both."""
    import time

    assert deploy._sleep is time.sleep
    assert deploy._monotonic is time.monotonic


# --- Fix round 1 ------------------------------------------------------------------------------------
# I-1: fabric-cicd's own log lines go through a redaction filter while publish runs.


@pytest.fixture
def fabric_cicd_console():
    """A stand-in for fabric-cicd 1.4.0's console handler on the `fabric_cicd` logger (configure_logger
    adds one at INFO when fabric_cicd is imported). Returns the stream it writes to."""
    import io
    import logging

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    package = logging.getLogger("fabric_cicd")
    level = package.level
    package.setLevel(logging.INFO)
    package.addHandler(handler)
    yield SimpleNamespace(stream=stream, handler=handler, package=package)
    package.removeHandler(handler)
    package.setLevel(level)


def _log_like_fabric_cicd():
    """The 1.4.0 binding lines (_items/_semanticmodel.py) with an ID, a URL and a traceback."""
    import logging

    logger = logging.getLogger("fabric_cicd._items._semanticmodel")
    logger.info(
        "Binding semantic model '%s' (ID: %s) to connection '%s'", "sm_portfolio", MODEL, MODEL_CONNECTION
    )
    try:
        raise ConnectionError(f"POST {REFRESHES_URL} failed")
    except ConnectionError as error:
        logger.error(f"Failed to bind 'sm_portfolio' to connection '{MODEL_CONNECTION}': {error!s}", exc_info=True)
    # a logger fabric-cicd creates after the filter is in place still reaches the same handler
    logging.getLogger("fabric_cicd._items._created_later").warning("workspace %s", WS)


@pytest.mark.parametrize("gates", ["closed", "open"])
def test_publish_redacts_ids_and_urls_in_fabric_cicd_logs(
    deploy, env, seams, monkeypatch, caplog, fabric_cicd_console, gates
):
    import logging

    if gates == "open":
        monkeypatch.setenv("P11_MODEL_CONNECTION_ID", MODEL_CONNECTION)
    original_workspace = deploy._fabric_workspace

    def workspace_that_logs(**kwargs):
        logging.getLogger("fabric_cicd.fabric_workspace").info("Workspace ID: %s", WS)
        return original_workspace(**kwargs)

    monkeypatch.setattr(deploy, "_fabric_workspace", workspace_that_logs)
    monkeypatch.setattr(deploy, "_publish", lambda workspace: _log_like_fabric_cicd())
    caplog.set_level(logging.INFO)

    assert deploy.main(["publish"]) == 0

    console = fabric_cicd_console.stream.getvalue()
    propagated = caplog.text  # the root logger's handlers (fabric-cicd's log file sits there)
    for text in (console, propagated):
        assert "Binding semantic model 'sm_portfolio' (ID: <id>) to connection '<id>'" in text
        assert "POST <url> failed" in text and "Traceback" in text
        assert "workspace <id>" in text and "Workspace ID: <id>" in text
        for value in (MODEL, MODEL_CONNECTION, WS, REFRESH_URL, "https://", "api.powerbi"):
            assert value not in text
    assert seams.ws_kwargs is not None  # the fake workspace still ran


@pytest.mark.parametrize(
    "text, redacted",
    [
        ("see https://a.example/x/y.", "see <url>."),
        ("(see https://a.example/x/y)", "(see <url>)"),
        ("(see https://a.example/x/y).", "(see <url>)."),
        ("a https://a.example/x, b https://b.example/y, c", "a <url>, b <url>, c"),
        ("failed: https://a.example/x?q=1.2,3).", "failed: <url>)."),
        ("POST https://a.example/x/y failed", "POST <url> failed"),
        ('url="https://a.example/x/y"', 'url="<url>"'),
        ("https://a.example/v1.0/x", "<url>"),
    ],
)
def test_redact_leaves_the_punctuation_after_a_url(deploy, text, redacted):
    assert deploy._redact(text) == redacted


def test_publish_removes_the_redaction_afterwards_even_on_failure(
    deploy, env, seams, monkeypatch, fabric_cicd_console
):
    import logging

    def failing_publish(workspace):
        raise RuntimeError("boom")

    monkeypatch.setattr(deploy, "_publish", failing_publish)
    assert deploy.main(["publish"]) == 1

    loggers = [logging.getLogger(), *(
        logging.getLogger(name) for name in logging.Logger.manager.loggerDict
        if name == "fabric_cicd" or name.startswith("fabric_cicd.") or name == "console_only"
    )]
    def ours(filterer):
        return [f for f in filterer.filters if isinstance(f, deploy._RedactingFilter)]

    for logger in loggers:
        assert ours(logger) == [], logger.name
        for handler in logger.handlers:
            assert ours(handler) == [], (logger.name, handler)
    # after publish, a fabric_cicd line is no longer rewritten
    logging.getLogger("fabric_cicd.later").warning("id %s", MODEL)
    assert MODEL in fabric_cicd_console.stream.getvalue()


# I-2: refresh-model checks the fixed-identity binding before it refreshes (ruling R7).


def test_refresh_model_checks_the_binding_before_refreshing(deploy, env, model):
    assert deploy.main(["refresh-model"]) == 0
    urls = [url for url, _ in model.gets]
    assert urls.index(CONNECTIONS_URL) < urls.index(HISTORY_URL)
    assert dict(model.gets)[CONNECTIONS_URL] == "tok-secret-value"  # a Fabric token
    assert len(model.posts) == 1


@pytest.mark.parametrize("entries", [
    [],
    [connection(None, "Automatic")],  # still on SSO: the bind was skipped
    [connection(OTHER_CONNECTION)],  # bound to another connection
    [connection(MODEL_CONNECTION, "PersonalCloud")],
    [connection(MODEL_CONNECTION, "None")],
    [{"id": MODEL_CONNECTION}],
    ["not-an-object"],
], ids=["none", "sso_only", "other_connection", "personal", "unbound", "no_type", "junk"])
def test_refresh_model_refuses_a_model_not_bound_to_its_connection(deploy, env, model, capsys, entries):
    model.routes[CONNECTIONS_URL] = (200, {"value": entries})

    assert deploy.main(["refresh-model"]) == 1

    out, err = capsys.readouterr()
    assert err == f"{NOT_BOUND}\n"
    assert out == "" and model.posts == []
    assert HISTORY_URL not in [url for url, _ in model.gets]
    for value in (WS, MODEL, MODEL_CONNECTION, OTHER_CONNECTION, "pbi-tok-secret", "tok-secret-value"):
        assert value not in out + err


def test_refresh_model_matches_the_connection_id_in_any_case(deploy, env, model):
    model.routes[CONNECTIONS_URL] = (200, {"value": [connection(MODEL_CONNECTION.upper())]})
    assert deploy.main(["refresh-model"]) == 0


def test_refresh_model_follows_the_connection_list_pages(deploy, env, model):
    model.routes[CONNECTIONS_URL] = (200, {"value": [connection(None, "Automatic")], "continuationToken": "a b"})
    model.routes[f"{CONNECTIONS_URL}?continuationToken=a%20b"] = (200, {"value": [connection()]})
    assert deploy.main(["refresh-model"]) == 0
    assert f"{CONNECTIONS_URL}?continuationToken=a%20b" in [url for url, _ in model.gets]


def test_refresh_model_stops_when_the_connection_list_repeats_a_token(deploy, env, model, capsys):
    model.routes[CONNECTIONS_URL] = (200, {"value": [], "continuationToken": "same"})
    model.routes[f"{CONNECTIONS_URL}?continuationToken=same"] = (200, {"value": [], "continuationToken": "same"})
    assert deploy.main(["refresh-model"]) == 1
    assert capsys.readouterr().err == "refresh-model: model connection lookup did not finish\n"
    assert model.posts == []


def test_refresh_model_connection_list_error_prints_status_and_code_only(deploy, env, model, capsys):
    model.routes[CONNECTIONS_URL] = (404, {"errorCode": "ItemNotFound", "message": f"{WS}/{MODEL}"})
    assert deploy.main(["refresh-model"]) == 1
    out, err = capsys.readouterr()
    assert err == "refresh-model: HTTP 404 ItemNotFound\n"
    assert WS not in out + err and MODEL not in out + err and model.posts == []


# Minor 1: a refresh that is already running is waited for first.


def test_refresh_model_waits_for_a_running_refresh_before_starting_its_own(deploy, env, model, capsys):
    model.routes[HISTORY_URL] = [history("Unknown"), history("Unknown"), history("Completed")]

    assert deploy.main(["refresh-model"]) == 0

    urls = [url for url, _ in model.gets]
    assert urls.count(HISTORY_URL) == 3
    assert model.sleeps == [deploy.REFRESH_POLL_SECONDS] * 3  # two for the earlier one, one for ours
    assert len(model.posts) == 1
    assert capsys.readouterr().out == "refresh-model: ok\n"


@pytest.mark.parametrize("latest", [history(), history("Failed"), history("Disabled"), (200, {})],
                         ids=["no_history", "failed", "disabled", "no_value"])
def test_refresh_model_starts_at_once_when_nothing_is_running(deploy, env, model, latest):
    """An earlier refresh that failed does not matter: this one replaces it."""
    model.routes[HISTORY_URL] = latest
    assert deploy.main(["refresh-model"]) == 0
    assert [url for url, _ in model.gets].count(HISTORY_URL) == 1
    assert len(model.posts) == 1


def test_refresh_model_gives_up_when_the_running_refresh_never_ends(deploy, env, model, capsys):
    model.routes[HISTORY_URL] = [history("Unknown")]
    assert deploy.main(["refresh-model"]) == 1
    out, err = capsys.readouterr()
    assert err == "refresh-model: an earlier refresh did not finish in 10 minutes\n"
    assert out == "" and model.posts == [] and sum(model.sleeps) == 600


def test_refresh_model_shares_one_10_minute_cap_with_the_wait(deploy, env, model, capsys):
    """Nine minutes waiting for the earlier refresh leave one for this one."""
    model.routes[HISTORY_URL] = [history("Unknown")] * 37 + [history("Completed")]  # 37 sleeps: 555 s
    model.routes[REFRESH_URL] = [refresh_state("InProgress")]
    assert deploy.main(["refresh-model"]) == 1
    assert capsys.readouterr().err == "refresh-model: refresh did not finish in 10 minutes\n"
    assert sum(model.sleeps) == 600 and len(model.posts) == 1


def test_refresh_model_history_error_prints_status_and_code_only(deploy, env, model, capsys):
    model.routes[HISTORY_URL] = (403, {"error": {"code": "PowerBINotAuthorizedException", "message": WS}})
    assert deploy.main(["refresh-model"]) == 1
    out, err = capsys.readouterr()
    assert err == "refresh-model: HTTP 403 PowerBINotAuthorizedException\n"
    assert WS not in out + err and model.posts == []


# Minor 2: the $ENV stand-in lives only while fabric-cicd reads the parameter file.


@pytest.mark.parametrize("value", [None, "", "set"])
@pytest.mark.parametrize("fails", [False, True], ids=["ok", "workspace_fails"])
def test_publish_leaves_the_environment_as_it_found_it(deploy, env, seams, monkeypatch, value, fails):
    for gate in ("P10_EVENTHUB_CONNECTION_ID", "P11_MODEL_CONNECTION_ID"):
        if value is not None:
            monkeypatch.setenv(gate, value if value != "set" else MODEL_CONNECTION)
    before = {gate: os.environ.get(gate) for gate in ("P10_EVENTHUB_CONNECTION_ID", "P11_MODEL_CONNECTION_ID")}
    seen = {}
    original_workspace = deploy._fabric_workspace

    def workspace(**kwargs):
        seen.update({gate: os.environ.get(gate) for gate in before})
        if fails:
            raise RuntimeError("bad parameter file")
        return original_workspace(**kwargs)

    def publish_items(workspace):
        seen["at_publish"] = {gate: os.environ.get(gate) for gate in before}

    monkeypatch.setattr(deploy, "_fabric_workspace", workspace)
    monkeypatch.setattr(deploy, "_publish", publish_items)

    assert deploy.main(["publish"]) == (1 if fails else 0)

    after = {gate: os.environ.get(gate) for gate in before}
    assert after == before
    for gate in before:
        assert (gate in os.environ) == (before[gate] is not None)
        assert seen[gate] == (before[gate] or EMPTY_GUID)  # the stand-in while the workspace is built
    if not fails:
        assert seen["at_publish"] == before  # and gone again before the items publish


# Minor 3: an unexpected error while starting or polling the refresh prints only its type.


@pytest.mark.parametrize("where", ["start", "poll", "history", "connections"])
def test_an_unexpected_error_in_refresh_model_prints_only_its_type(deploy, env, model, monkeypatch, capsys, where):
    failing_url = {"poll": REFRESH_URL, "history": HISTORY_URL, "connections": CONNECTIONS_URL}.get(where)
    real_get = deploy._http_get

    def get(url, token):
        if url == failing_url:
            raise ConnectionError(f"GET {url} with {token}")
        return real_get(url, token)

    def post(url, json, token):
        raise ConnectionError(f"POST {url} with {token}")

    monkeypatch.setattr(deploy, "_http_get", get)
    if where == "start":
        monkeypatch.setattr(deploy, "_http_post_location", post)

    assert deploy.main(["refresh-model"]) == 1

    out, err = capsys.readouterr()
    assert err == "refresh-model: failed (ConnectionError)\n"
    assert out == ""
    for value in (WS, MODEL, REFRESH, MODEL_CONNECTION, "https://", "pbi-tok-secret", "tok-secret-value", "Traceback"):
        assert value not in out + err


# --- Capstone: publish-capstone (deployed by hand as fabric@, never from deploy.yml) ----------------

CAPSTONE_DIR = DEPLOY_PY.parent.parent / "fabric-capstone"
CAPSTONE_PARAMETERS = CAPSTONE_DIR / "parameter.yml"
DATASOURCE = (
    CAPSTONE_DIR / "da_portfolio.DataAgent" / "Files" / "Config" / "draft"
    / "lakehouse-tables-lh_capstone" / "datasource.json"
)
CAPSTONE_TYPES = ["Lakehouse", "Notebook", "DataAgent"]
DATA_AGENT = "eeeeeeee-aaaa-bbbb-cccc-00000000000e"
OTHER_AGENT = "ffffffff-aaaa-bbbb-cccc-00000000000f"
CAPSTONE_LH = "12121212-aaaa-bbbb-cccc-000000000012"
AGENT_LIST_URL = f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/dataAgents"
# Learn, Items - Publish Data Agent (DataAgent): POST .../dataAgents/{dataAgentId}/staging/publish,
# body {publishedDescription}, 200 OK (not a long-running operation).
PUBLISH_AGENT_URL = f"{AGENT_LIST_URL}/{DATA_AGENT}/staging/publish"
# What fabric-cicd resolves the two dynamic variables to on a CAPSTONE deploy (made-up IDs).
CAPSTONE_RESOLVED = {"$workspace.$id": WS, "$items.Lakehouse.lh_capstone.$id": CAPSTONE_LH}


def agent_list(*items, token=None):
    body = {"value": [{"type": "DataAgent", **item} for item in items]}
    if token:
        body["continuationToken"] = token
    return 200, body


@pytest.fixture
def capstone(deploy, monkeypatch, seams):
    """The capstone workspace holds da_portfolio (after another agent); the Publish call answers 200.
    Every seam call lands in `events`, in order."""
    seams.routes = {
        **(seams.routes or {}),
        AGENT_LIST_URL: agent_list(
            {"displayName": "da_other", "id": OTHER_AGENT}, {"displayName": "da_portfolio", "id": DATA_AGENT}
        ),
    }
    seams.response = (200, {"publishedDescription": "anything"})
    real_get, real_post, real_publish = deploy._http_get, deploy._http_post, deploy._publish

    def get(url, token):
        seams.events.append(("get", url))
        return real_get(url, token)

    def post(url, json, token):
        seams.events.append(("post", url))
        return real_post(url, json, token)

    def publish(workspace):
        seams.events.append("publish")
        real_publish(workspace)

    monkeypatch.setattr(deploy, "_http_get", get)
    monkeypatch.setattr(deploy, "_http_post", post)
    monkeypatch.setattr(deploy, "_publish", publish)
    monkeypatch.setattr(deploy, "_load_fabric_cicd", lambda: seams.events.append("load"))
    return seams


def test_publish_capstone_deploys_fabric_capstone_then_publishes_the_agent(deploy, env, capstone, capsys):
    assert deploy.main(["publish-capstone"]) == 0
    assert capstone.ws_kwargs == {
        "workspace_id": WS,
        "environment": "CAPSTONE",
        "repository_directory": str(CAPSTONE_DIR),
        "item_type_in_scope": CAPSTONE_TYPES,
        "token_credential": capstone.cred,
    }
    assert Path(capstone.ws_kwargs["repository_directory"]).is_absolute()
    assert capstone.published == ["WS-OBJECT"]
    # fabric-cicd first (it creates or updates da_portfolio), then the lookup, then Publish
    assert capstone.events == [
        "load", "workspace", "publish", ("get", AGENT_LIST_URL), ("post", PUBLISH_AGENT_URL),
    ]
    (url, body, token), = capstone.posts
    assert url == PUBLISH_AGENT_URL
    assert body == {"publishedDescription": deploy.DATA_AGENT_DESCRIPTION}
    assert token == "tok-secret-value"
    assert capstone.cred.calls == [(FABRIC_SCOPE,)]
    out, err = capsys.readouterr()
    assert out == "publish-capstone: ok\n" and err == ""


def test_publish_capstone_description_is_a_plain_sentence(deploy):
    description = deploy.DATA_AGENT_DESCRIPTION
    assert isinstance(description, str) and 0 < len(description) <= 500
    assert "lh_capstone" in description
    assert not re.search(r"https?://|@|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-", description)


def test_publish_capstone_needs_no_gate_no_environment_name_and_no_env_replacement(
    deploy, env, capstone, monkeypatch
):
    """Always explicit: the P10 and P11 gates are unset (the autouse fixtures), FABRIC_ENVIRONMENT is
    not read, and the $ENV feature flag and its stand-ins are not used."""
    monkeypatch.delenv("FABRIC_ENVIRONMENT")
    assert deploy.main(["publish-capstone"]) == 0
    assert capstone.ws_kwargs["environment"] == "CAPSTONE"
    assert not any(isinstance(e, tuple) and e[0] == "flag" for e in capstone.events)
    assert capstone.env_at_build is None and capstone.model_env_at_build is None
    assert "P10_EVENTHUB_CONNECTION_ID" not in os.environ and "P11_MODEL_CONNECTION_ID" not in os.environ


def test_publish_capstone_ignores_the_prod_environment_name(deploy, env, capstone):
    assert env["FABRIC_ENVIRONMENT"] == "PROD"
    assert deploy.main(["publish-capstone"]) == 0
    assert capstone.ws_kwargs["environment"] == "CAPSTONE"


def test_publish_capstone_missing_workspace_names_the_variable_only(deploy, env, capstone, monkeypatch, capsys):
    monkeypatch.delenv("FABRIC_WORKSPACE_ID")
    assert deploy.main(["publish-capstone"]) == 2
    out, err = capsys.readouterr()
    assert err == "missing environment variable FABRIC_WORKSPACE_ID\n" and out == ""
    assert capstone.ws_kwargs is None and capstone.gets == [] and capstone.posts == []


def test_publish_capstone_agent_lookup_follows_continuation_tokens(deploy, env, capstone):
    pages = {
        AGENT_LIST_URL: agent_list({"displayName": "da_other", "id": OTHER_AGENT}, token="a b/c"),
        f"{AGENT_LIST_URL}?continuationToken=a%20b%2Fc": agent_list(
            {"displayName": "da_portfolio", "id": DATA_AGENT}
        ),
    }
    capstone.routes.update(pages)
    assert deploy.main(["publish-capstone"]) == 0
    assert [url for url, _ in capstone.gets] == list(pages)
    assert capstone.posts[0][0] == PUBLISH_AGENT_URL


def test_publish_capstone_agent_lookup_stops_when_a_continuation_token_repeats(deploy, env, capstone, capsys):
    capstone.routes[AGENT_LIST_URL] = agent_list({"displayName": "da_other", "id": OTHER_AGENT}, token="t")
    capstone.routes[f"{AGENT_LIST_URL}?continuationToken=t"] = agent_list(
        {"displayName": "da_other", "id": OTHER_AGENT}, token="t"
    )
    assert deploy.main(["publish-capstone"]) == 1
    out, err = capsys.readouterr()
    assert err == "publish-capstone: data agent lookup did not finish\n"
    assert out == "" and capstone.posts == []


def test_publish_capstone_without_the_agent_fails_without_ids(deploy, env, capstone, capsys):
    capstone.routes[AGENT_LIST_URL] = agent_list({"displayName": "da_other", "id": OTHER_AGENT})
    assert deploy.main(["publish-capstone"]) == 1
    out, err = capsys.readouterr()
    assert err == "publish-capstone: data agent da_portfolio not found\n"
    assert out == "" and capstone.posts == []
    for value in (WS, OTHER_AGENT):
        assert value not in out + err


def test_publish_capstone_lookup_error_prints_status_and_code_only(deploy, env, capstone, capsys):
    capstone.routes[AGENT_LIST_URL] = (403, {"errorCode": "InsufficientPrivileges", "message": f"ws {WS}"})
    assert deploy.main(["publish-capstone"]) == 1
    out, err = capsys.readouterr()
    assert err == "publish-capstone: HTTP 403 InsufficientPrivileges\n"
    assert out == "" and capstone.posts == [] and WS not in err


@pytest.mark.parametrize("response, line", [
    ((404, {"errorCode": "ItemNotFound", "message": f"agent {DATA_AGENT} in {WS}"}), "HTTP 404 ItemNotFound"),
    ((400, {"errorCode": "InvalidRequest", "requestId": DATA_AGENT}), "HTTP 400 InvalidRequest"),
    ((429, {"message": f"slow down {PUBLISH_AGENT_URL}"}), "HTTP 429 unknown"),
    ((500, "not-json-object"), "HTTP 500 unknown"),
    ((403, {"errorCode": f"{PUBLISH_AGENT_URL} injected"}), "HTTP 403 unknown"),
    # Learn documents 200 only: a 202 would be an operation this step does not wait for, so it fails
    ((202, {}), "HTTP 202 unknown"),
])
def test_publish_capstone_publish_error_prints_status_and_code_only(deploy, env, capstone, capsys, response, line):
    capstone.response = response
    assert deploy.main(["publish-capstone"]) == 1
    out, err = capsys.readouterr()
    assert err == f"publish-capstone: {line}\n"
    assert out == ""
    for value in (WS, DATA_AGENT, PUBLISH_AGENT_URL, "https://", "tok-secret-value", "slow down"):
        assert value not in out + err


@pytest.mark.parametrize("where", ["workspace", "publish", "lookup", "publish-call"])
def test_an_unexpected_error_in_publish_capstone_prints_only_its_type(
    deploy, env, capstone, monkeypatch, capsys, where
):
    url = f"https://x/workspaces/{WS}/dataAgents/{DATA_AGENT}"

    def fail(*args, **kwargs):
        raise ConnectionError(f"{url} tok-secret-value")

    target = {"workspace": "_fabric_workspace", "publish": "_publish", "lookup": "_http_get",
              "publish-call": "_http_post"}[where]
    monkeypatch.setattr(deploy, target, fail)
    assert deploy.main(["publish-capstone"]) == 1
    out, err = capsys.readouterr()
    assert err == "publish-capstone: failed (ConnectionError)\n" and out == ""
    for value in (url, WS, DATA_AGENT, "https://", "tok-secret-value", "Traceback"):
        assert value not in out + err
    if where in ("workspace", "publish"):
        assert capstone.gets == [] and capstone.posts == []  # no Publish after a failed deploy


def test_publish_capstone_redacts_fabric_cicd_logs_and_removes_the_filter_afterwards(
    deploy, env, capstone, monkeypatch, caplog, fabric_cicd_console
):
    import logging

    def publish_that_logs(workspace):
        capstone.events.append("publish")
        logging.getLogger("fabric_cicd.fabric_workspace").info("Workspace ID: %s", WS)
        logging.getLogger("fabric_cicd._items._dataagent").info(
            "Published DataAgent 'da_portfolio' (%s) via %s", DATA_AGENT, PUBLISH_AGENT_URL
        )

    monkeypatch.setattr(deploy, "_publish", publish_that_logs)
    caplog.set_level(logging.INFO)
    assert deploy.main(["publish-capstone"]) == 0

    for text in (fabric_cicd_console.stream.getvalue(), caplog.text):
        assert "Workspace ID: <id>" in text
        assert "Published DataAgent 'da_portfolio' (<id>) via <url>" in text
        for value in (WS, DATA_AGENT, "https://"):
            assert value not in text
    for target in [fabric_cicd_console.package, *fabric_cicd_console.package.handlers]:
        assert not [f for f in target.filters if isinstance(f, deploy._RedactingFilter)]


def test_publish_capstone_loads_fabric_cicd_before_the_redaction_is_in_place(deploy, env, capstone, monkeypatch):
    """fabric-cicd adds its console handler when it is imported; the redacting filter sits on the
    handlers present when the block starts, so the import must come first."""
    seen = {}
    real = deploy._redacted_fabric_cicd_logs

    def redacted():
        seen["events_at_redaction"] = list(capstone.events)
        return real()

    monkeypatch.setattr(deploy, "_redacted_fabric_cicd_logs", redacted)
    assert deploy.main(["publish-capstone"]) == 0
    assert seen["events_at_redaction"] == ["load"]


def test_load_fabric_cicd_imports_the_package(deploy, monkeypatch):
    import sys

    imported = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "fabric_cicd", imported)
    assert deploy._load_fabric_cicd() is None
    monkeypatch.setitem(sys.modules, "fabric_cicd", None)  # import fabric_cicd now raises
    with pytest.raises(ImportError):
        deploy._load_fabric_cicd()


def test_the_prod_publish_scope_is_unchanged_and_never_reaches_the_capstone(deploy, env, realtime, model):
    assert deploy.ITEM_TYPES_IN_SCOPE == [
        "Lakehouse", "Notebook", "DataPipeline", *REALTIME_TYPES, *MODEL_TYPES,
    ]
    assert "DataAgent" not in deploy.ITEM_TYPES_IN_SCOPE
    assert deploy.main(["publish"]) == 0
    assert realtime.ws_kwargs["repository_directory"] == str(DEPLOY_PY.parent.parent / "fabric")
    assert realtime.ws_kwargs["environment"] == "PROD"


# The parameter file fabric-cicd 1.4.0 reads for this repository directory: with no parameter_file_path
# it takes <repository_directory>/parameter.yml (_parameter/_parameter.py, _set_parameter_file_path).


def _capstone_parameters() -> dict:
    return yaml.safe_load(CAPSTONE_PARAMETERS.read_text(encoding="utf-8"))


def test_the_capstone_has_its_own_parameter_file_and_publish_capstone_points_no_other(deploy, env, capstone):
    assert CAPSTONE_PARAMETERS.is_file()
    assert deploy.main(["publish-capstone"]) == 0
    assert "parameter_file_path" not in capstone.ws_kwargs
    assert Path(capstone.ws_kwargs["repository_directory"]) / "parameter.yml" == CAPSTONE_PARAMETERS


def test_capstone_parameter_file_holds_exactly_the_two_datasource_rules():
    params = _capstone_parameters()
    assert set(params) == {"find_replace"}
    rules = params["find_replace"]
    assert len(rules) == 2
    for rule in rules:
        assert set(rule) == {"find_value", "replace_value", "is_regex", "item_type", "file_path"}
        assert rule["is_regex"] == "true"  # a string, as fabric-cicd's _validate_find_regex needs
        assert rule["item_type"] == "DataAgent" and rule["item_type"] in CAPSTONE_TYPES
        assert rule["file_path"] == "**/datasource.json"
        assert set(rule["replace_value"]) == {"CAPSTONE"}
        assert re.compile(rule["find_value"]).groups == 1  # fabric-cicd's _validate_regex_structure
    assert sorted(r["replace_value"]["CAPSTONE"] for r in rules) == sorted(CAPSTONE_RESOLVED)


def test_capstone_rules_name_only_items_the_capstone_deploys():
    raw = CAPSTONE_PARAMETERS.read_text(encoding="utf-8")
    named = set(re.findall(r"\$items\.([A-Za-z]+)\.([^.$\s\"']+)\.\$", raw))
    assert named == {("Lakehouse", "lh_capstone")}
    for item_type, item_name in named:
        assert item_type in CAPSTONE_TYPES
        assert (CAPSTONE_DIR / f"{item_name}.{item_type}").is_dir()
    assert "$ENV:" not in raw  # no environment-variable replacement, so no feature flag


def test_capstone_rules_apply_only_to_the_one_datasource_file():
    """fabric-cicd applies a rule to a file when the item type matches and the glob (resolved
    against the repository directory) lists it; each regex matches that file exactly once and no
    other file of the data agent."""
    agent_files = [p for p in (CAPSTONE_DIR / "da_portfolio.DataAgent").rglob("*") if p.is_file()]
    text = DATASOURCE.read_text(encoding="utf-8")
    for rule in _capstone_parameters()["find_replace"]:
        assert sorted(CAPSTONE_DIR.glob(rule["file_path"])) == [DATASOURCE]
        assert len(re.findall(rule["find_value"], text)) == 1, rule["find_value"]
        for path in agent_files:
            if path != DATASOURCE:
                assert not re.search(rule["find_value"], path.read_text(encoding="utf-8")), path


def test_capstone_rules_are_anchored_on_their_field_names():
    text = DATASOURCE.read_text(encoding="utf-8")
    fields = {}
    for rule in _capstone_parameters()["find_replace"]:
        match = re.search(rule["find_value"], text)
        assert match.group(1) == EMPTY_GUID
        field = re.match(r'"(\w+)"', match.group(0)).group(1)
        fields[field] = rule["replace_value"]["CAPSTONE"]
    assert fields == {"workspaceId": "$workspace.$id", "artifactId": "$items.Lakehouse.lh_capstone.$id"}


@pytest.mark.parametrize("order", [1, -1], ids=["as_written", "reversed"])
def test_capstone_rules_change_only_the_two_ids_in_the_datasource(order):
    """As fabric-cicd 1.4.0 applies them in turn (fabric_workspace.py, _replace_parameters): the second
    still matches after the first, and nothing else in the file changes."""
    from test_fabric_items import _apply_find_replace

    text = DATASOURCE.read_text(encoding="utf-8")
    result = text
    for rule in _capstone_parameters()["find_replace"][::order]:
        assert len(re.findall(rule["find_value"], result)) == 1
        result = _apply_find_replace(rule, result, CAPSTONE_RESOLVED[rule["replace_value"]["CAPSTONE"]])

    before, after = json.loads(text), json.loads(result)
    assert after["workspaceId"] == WS and after["artifactId"] == CAPSTONE_LH
    assert {k: v for k, v in after.items() if k not in ("workspaceId", "artifactId")} == {
        k: v for k, v in before.items() if k not in ("workspaceId", "artifactId")
    }
    assert result.replace(WS, EMPTY_GUID).replace(CAPSTONE_LH, EMPTY_GUID) == text


def test_the_prod_parameter_file_knows_nothing_of_the_capstone():
    raw = (DEPLOY_PY.parent.parent / "fabric" / "parameter.yml").read_text(encoding="utf-8")
    for value in ("CAPSTONE", "capstone", "DataAgent", "datasource.json"):
        assert value not in raw
