"""Tests for .deploy/deploy.py. Every Azure/Fabric call sits behind a seam the
tests replace; nothing here touches the network."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    "with (IsEnabled=true, TargetLatencyInMinutes=5)"
)
REALTIME_TYPES = ["Eventhouse", "KQLDatabase", "KQLQueryset", "KQLDashboard", "Reflex", "Eventstream"]
SKIP_REASON = "(no P10_EVENTHUB_CONNECTION_ID)"


@pytest.fixture(autouse=True)
def no_realtime_gate(monkeypatch):
    """Every test starts with P10_EVENTHUB_CONNECTION_ID unset, and whatever publish writes to it is
    undone afterwards (setenv first, so monkeypatch records the variable and restores its absence)."""
    monkeypatch.setenv("P10_EVENTHUB_CONNECTION_ID", "unset-by-fixture")
    monkeypatch.delenv("P10_EVENTHUB_CONNECTION_ID")


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
        token = "kusto-tok-secret" if scopes == (KUSTO_SCOPE,) else "tok-secret-value"
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
                            ]})], routes=None, events=[], env_at_build=None)

    def datalake_client(credential):
        assert credential is cred
        return FakeDatalake(state.upload)

    def http_post(url, json, token):
        state.posts.append((url, json, token))
        return state.response

    def http_get(url, token):
        state.gets.append((url, token))
        if state.routes is not None:
            return state.routes[url]
        return state.pages[len(state.gets) - 1]

    def fabric_workspace(**kwargs):
        state.events.append("workspace")
        state.env_at_build = os.environ.get("P10_EVENTHUB_CONNECTION_ID")
        state.ws_kwargs = kwargs
        return "WS-OBJECT"

    monkeypatch.setattr(deploy, "_datalake_client", datalake_client)
    monkeypatch.setattr(deploy, "_http_post", http_post)
    monkeypatch.setattr(deploy, "_http_get", http_get)
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


def test_an_unexpected_error_in_publish_prints_only_its_type(deploy, env, realtime, monkeypatch, capsys):
    # realtime: with the gate off, publish would also print its skip line before the failure
    url = f"https://x/workspaces/{WS}/items"

    def failing_publish(workspace):
        raise RuntimeError(f"publish to {url} failed")

    monkeypatch.setattr(deploy, "_publish", failing_publish)

    assert deploy.main(["publish"]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == "publish: failed (RuntimeError)"
    assert out == ""
    for value in (url, WS, "https://x", "Traceback", EVENTHUB_CONNECTION):
        assert value not in out + err


@pytest.mark.parametrize(
    "command", ["upload-wheel", "ensure-shortcut", "onelake-availability", "ensure-gateway-shortcut"]
)
def test_an_unexpected_error_prints_only_its_type(deploy, env, realtime, monkeypatch, capsys, command):
    """No message, URL, workspace ID or traceback: a ConnectionError text carries the request URL."""
    url = f"https://x/workspaces/{WS}/lakehouses"

    def failing_get(requested, token):
        raise ConnectionError(url)

    monkeypatch.setattr(deploy, "_http_get", failing_get)

    assert deploy.main([command]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == f"{command}: failed (ConnectionError)"
    assert out == ""
    for value in (url, WS, "https://x", "Traceback", "tok-secret-value", EVENTHUB_CONNECTION):
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
])
def test_missing_env_per_command(deploy, env, realtime, monkeypatch, capsys, command, name):
    monkeypatch.delenv(name)
    assert deploy.main([command]) == 2
    out, err = capsys.readouterr()
    assert err.strip() == f"missing environment variable {name}"
    assert EVENTHUB_CONNECTION not in out + err
    assert realtime.gets == [] and realtime.posts == []


ALL_COMMANDS = (
    "upload-wheel", "publish", "ensure-shortcut", "onelake-availability", "ensure-gateway-shortcut",
)


def test_no_secret_in_output(deploy, env, realtime, capsys):
    for command in ALL_COMMANDS:
        assert deploy.main([command]) == 0, command
    realtime.response = (500, {"errorCode": "Boom", "message": LOCATION})
    for command in ("ensure-shortcut", "onelake-availability", "ensure-gateway-shortcut"):
        assert deploy.main([command]) == 1, command
    out, err = capsys.readouterr()
    for value in list(env.values()) + [
        "tok-secret-value", "kusto-tok-secret", LH, OTHER_LH, KQL, OTHER_KQL,
        EVENTHUB_CONNECTION, QUERY_URI, "qs-secret-host",
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
    assert deploy.ITEM_TYPES_IN_SCOPE == realtime.ws_kwargs["item_type_in_scope"]
    assert realtime.env_at_build == EVENTHUB_CONNECTION  # left as it is
    out, err = capsys.readouterr()
    assert out.splitlines() == ["publish: done"]
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
