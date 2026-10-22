"""Tests for .deploy/deploy.py. Every Azure/Fabric call sits behind a seam the
tests replace; nothing here touches the network."""

import importlib.util
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


@pytest.fixture
def deploy():
    spec = importlib.util.spec_from_file_location("deploy_script", DEPLOY_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCredential:
    def get_token(self, *scopes):
        self.scopes = scopes
        return SimpleNamespace(token="tok-secret-value")


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
                            ]})])

    def datalake_client(credential):
        assert credential is cred
        return FakeDatalake(state.upload)

    def http_post(url, json, token):
        state.posts.append((url, json, token))
        return state.response

    def http_get(url, token):
        state.gets.append((url, token))
        return state.pages[len(state.gets) - 1]

    def fabric_workspace(**kwargs):
        state.ws_kwargs = kwargs
        return "WS-OBJECT"

    monkeypatch.setattr(deploy, "_datalake_client", datalake_client)
    monkeypatch.setattr(deploy, "_http_post", http_post)
    monkeypatch.setattr(deploy, "_http_get", http_get)
    monkeypatch.setattr(deploy, "_fabric_workspace", fabric_workspace)
    monkeypatch.setattr(deploy, "_publish", state.published.append)
    return state


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
    assert err.strip() == "ensure-shortcut: failed (ValueError)"
    assert out == "" and "exports/costs" not in err
    assert seams.posts == []


@pytest.mark.parametrize("command", ["upload-wheel", "ensure-shortcut"])
def test_an_unexpected_error_prints_only_its_type(deploy, env, seams, monkeypatch, capsys, command):
    """No message, URL, workspace ID or traceback: a ConnectionError text carries the request URL."""
    url = f"https://x/workspaces/{WS}/lakehouses"

    def failing_get(requested, token):
        raise ConnectionError(url)

    monkeypatch.setattr(deploy, "_http_get", failing_get)

    assert deploy.main([command]) == 1
    out, err = capsys.readouterr()
    assert err.strip() == f"{command}: failed (ConnectionError)"
    assert out == ""
    for value in (url, WS, "https://x", "Traceback", "tok-secret-value"):
        assert value not in out + err
    assert seams.upload == {} and seams.posts == []


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
])
def test_missing_env_per_command(deploy, env, seams, monkeypatch, capsys, command, name):
    monkeypatch.delenv(name)
    assert deploy.main([command]) == 2
    assert capsys.readouterr().err.strip() == f"missing environment variable {name}"


def test_no_secret_in_output(deploy, env, seams, capsys):
    for command in ("upload-wheel", "publish", "ensure-shortcut"):
        assert deploy.main([command]) == 0
        seams.gets.clear()
    seams.response = (500, {"errorCode": "Boom", "message": LOCATION})
    assert deploy.main(["ensure-shortcut"]) == 1
    out, err = capsys.readouterr()
    for value in list(env.values()) + ["tok-secret-value", LH, OTHER_LH]:
        assert value not in out + err
