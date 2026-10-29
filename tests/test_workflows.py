"""Structural checks on the GitHub workflows. They parse the YAML only: nothing here runs a
workflow or touches GitHub."""

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
SHA_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
SECRET_EXPRESSION = re.compile(r"\$\{\{\s*secrets\.", re.IGNORECASE)


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(workflow):
    # YAML 1.1 parses the bare key `on` as the boolean True.
    block = workflow["on"] if "on" in workflow else workflow[True]
    if isinstance(block, str):
        return {block: None}
    if isinstance(block, list):
        return {name: None for name in block}
    return block


def steps(job):
    return job.get("steps", [])


def all_steps(workflow):
    for job_name, job in workflow["jobs"].items():
        for step in steps(job):
            yield job_name, step


@pytest.fixture(params=["ci.yml", "deploy.yml"])
def any_workflow(request):
    return load(request.param)


@pytest.fixture
def deploy():
    return load("deploy.yml")


@pytest.fixture
def ci():
    return load("ci.yml")


def logs_in(job):
    return any(str(step.get("uses", "")).startswith("azure/login@") for step in steps(job))


def test_deploy_has_no_pull_request_trigger(deploy):
    on = triggers(deploy)
    assert "pull_request" not in on
    assert "pull_request_target" not in on
    assert "workflow_dispatch" not in on
    assert set(on) == {"push"}
    assert set(on["push"]["branches"]) == {"dev", "main"}


def test_deploy_has_concurrency_and_timeouts(deploy):
    assert deploy["concurrency"]["group"] == "deploy-${{ github.ref }}"
    for name, job in deploy["jobs"].items():
        assert job["timeout-minutes"] == 30, name


def test_login_jobs_have_id_token_write_and_an_environment(deploy):
    login_jobs = {n: j for n, j in deploy["jobs"].items() if logs_in(j)}
    assert set(login_jobs) == {"dev", "prod"}
    for name, job in login_jobs.items():
        assert job["permissions"]["id-token"] == "write", name
        assert job["permissions"]["contents"] == "read", name
        assert job.get("environment") == name, name


def test_build_job_is_read_only_and_uploads_the_wheel(deploy):
    build = deploy["jobs"]["build"]
    assert build["permissions"] == {"contents": "read"}
    assert "environment" not in build
    assert not logs_in(build)
    uses = [str(s.get("uses", "")) for s in steps(build)]
    assert any(u.startswith("actions/upload-artifact@") for u in uses)
    assert any("python -m build --wheel" in str(s.get("run", "")) for s in steps(build))


def test_prod_runs_only_on_main_and_dev_only_on_dev(deploy):
    assert deploy["jobs"]["prod"]["if"] == "github.ref == 'refs/heads/main'"
    assert deploy["jobs"]["dev"]["if"] == "github.ref == 'refs/heads/dev'"
    for name in ("dev", "prod"):
        assert deploy["jobs"][name]["needs"] == "build"


def deploy_commands(job):
    found = []
    for step in steps(job):
        match = re.search(r"deploy\.py\s+(\S+)", str(step.get("run", "")))
        if match:
            found.append(match.group(1))
    return found


def test_dev_uploads_and_shortcuts_but_does_not_publish(deploy):
    assert deploy_commands(deploy["jobs"]["dev"]) == ["upload-wheel", "ensure-shortcut"]


def test_prod_publishes_then_uploads_then_shortcuts(deploy):
    # publish first: on a first deploy the lakehouse the wheel and the shortcut go into exists
    # only after it. OneLake availability comes before the gateway shortcut, which targets the
    # table that availability exposes. The model refresh comes last, after every table it frames
    # (the gateway table through its shortcut) is in place.
    assert deploy_commands(deploy["jobs"]["prod"]) == [
        "publish",
        "upload-wheel",
        "ensure-shortcut",
        "onelake-availability",
        "ensure-gateway-shortcut",
        "refresh-model",
    ]


@pytest.mark.parametrize("name", ["deploy.yml", "ci.yml"])
def test_no_workflow_touches_the_capstone(name):
    """The capstone's data agent is deployed by hand as fabric@ (publish-capstone), never from
    Actions, whether by the deploy service principal or by CI."""
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    # The one exception: CI checks the capstone's Terraform stacks (infra/capstone/*), format and
    # validate only, as for every stack. That is infrastructure, not the data agent.
    text = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("terraform -chdir=infra/capstone/")
    )
    for value in ("publish-capstone", "fabric-capstone", "CAPSTONE_WORKSPACE_ID", "capstone", "CAPSTONE"):
        assert value not in text


def test_deploy_runs_no_command_outside_the_projects_9_to_11_set(deploy):
    allowed = {
        "publish", "upload-wheel", "ensure-shortcut", "onelake-availability", "ensure-gateway-shortcut",
        "refresh-model",
    }
    for job in deploy["jobs"].values():
        assert set(deploy_commands(job)) <= allowed


GATE = "P10_EVENTHUB_CONNECTION_ID"
GATE_SECRET = "${{ secrets.P10_EVENTHUB_CONNECTION_ID }}"


def prod_step(deploy, command):
    return next(
        s for s in steps(deploy["jobs"]["prod"]) if f"deploy.py {command}" in str(s.get("run", ""))
    )


def test_prod_publish_gets_the_eventhub_connection_gate(deploy):
    assert prod_step(deploy, "publish")["env"][GATE] == GATE_SECRET


@pytest.mark.parametrize("command, name", [
    ("onelake-availability", "Make the gateway table available in OneLake"),
    ("ensure-gateway-shortcut", "Ensure the gateway shortcut"),
])
def test_prod_realtime_steps_take_the_workspace_and_the_gate_at_step_scope(deploy, command, name):
    step = prod_step(deploy, command)
    assert step["name"] == name
    assert step["env"] == {
        "FABRIC_WORKSPACE_ID": "${{ secrets.FABRIC_WORKSPACE_ID }}",
        GATE: GATE_SECRET,
    }
    assert step["run"].strip() == f"python .deploy/deploy.py {command}"


def test_the_dev_job_has_no_realtime_steps(deploy):
    for step in steps(deploy["jobs"]["dev"]):
        assert GATE not in (step.get("env") or {})
        assert "onelake-availability" not in str(step.get("run", ""))
        assert "ensure-gateway-shortcut" not in str(step.get("run", ""))


MODEL_GATE = "P11_MODEL_CONNECTION_ID"
MODEL_GATE_SECRET = "${{ secrets.P11_MODEL_CONNECTION_ID }}"


def test_prod_publish_gets_the_model_connection_gate(deploy):
    """fabric-cicd's semantic_model_binding reads it through parameter.yml's $ENV: token."""
    assert prod_step(deploy, "publish")["env"] == {
        "FABRIC_WORKSPACE_ID": "${{ secrets.FABRIC_WORKSPACE_ID }}",
        "FABRIC_ENVIRONMENT": "PROD",
        GATE: GATE_SECRET,
        MODEL_GATE: MODEL_GATE_SECRET,
    }


def test_prod_refreshes_the_model_after_the_gateway_shortcut_with_its_gate_at_step_scope(deploy):
    """refresh-model reads P11_MODEL_CONNECTION_ID twice: as its gate, and as the connection it
    checks the model is bound to (R7) before the refresh."""
    job_steps = steps(deploy["jobs"]["prod"])
    names = [s.get("name") for s in job_steps]
    refresh = names.index("Refresh the semantic model")
    assert names[refresh - 1] == "Ensure the gateway shortcut"
    assert refresh == len(job_steps) - 1
    step = job_steps[refresh]
    assert step["env"] == {
        "FABRIC_WORKSPACE_ID": "${{ secrets.FABRIC_WORKSPACE_ID }}",
        MODEL_GATE: MODEL_GATE_SECRET,
    }
    assert step["run"].strip() == "python .deploy/deploy.py refresh-model"


def test_only_publish_and_the_refresh_take_the_model_gate(deploy):
    for job_name, step in all_steps(deploy):
        run = str(step.get("run", ""))
        if job_name == "prod" and ("deploy.py publish" in run or "deploy.py refresh-model" in run):
            continue
        assert MODEL_GATE not in (step.get("env") or {}), (job_name, step.get("name"))
        assert "refresh-model" not in run, job_name


@pytest.mark.parametrize("job", ["dev", "prod"])
def test_install_comes_before_login_and_login_directly_before_the_first_deploy_step(deploy, job):
    job_steps = steps(deploy["jobs"][job])
    install = next(
        i for i, s in enumerate(job_steps) if 'pip install ".[deploy]"' in str(s.get("run", ""))
    )
    login = next(
        i for i, s in enumerate(job_steps) if str(s.get("uses", "")).startswith("azure/login@")
    )
    first_deploy = next(
        i for i, s in enumerate(job_steps) if "deploy.py" in str(s.get("run", ""))
    )
    assert install < login
    assert login + 1 == first_deploy


def test_no_workflow_takes_a_lakehouse_id(any_workflow):
    for job_name, step in all_steps(any_workflow):
        assert "FABRIC_LAKEHOUSE_ID" not in (step.get("env") or {}), job_name
        assert "FABRIC_LAKEHOUSE_ID" not in str(step.get("run", "")), job_name


def test_every_checkout_does_not_persist_credentials(any_workflow):
    checkouts = [s for _, s in all_steps(any_workflow) if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert checkouts
    for step in checkouts:
        assert (step.get("with") or {}).get("persist-credentials") is False


def test_prod_publishes_into_the_prod_environment(deploy):
    publish = next(
        s for s in steps(deploy["jobs"]["prod"]) if "deploy.py publish" in str(s.get("run", ""))
    )
    assert publish["env"]["FABRIC_ENVIRONMENT"] == "PROD"
    assert publish["env"]["FABRIC_WORKSPACE_ID"] == "${{ secrets.FABRIC_WORKSPACE_ID }}"


def test_login_takes_ids_from_environment_secrets(deploy):
    for name in ("dev", "prod"):
        login = next(
            s for s in steps(deploy["jobs"][name]) if str(s.get("uses", "")).startswith("azure/login@")
        )
        assert login["with"]["client-id"] == "${{ secrets.AZURE_CLIENT_ID }}"
        assert login["with"]["tenant-id"] == "${{ secrets.AZURE_TENANT_ID }}"
        assert login["with"]["allow-no-subscriptions"] is True
        assert "subscription-id" not in login["with"]
        assert "client-secret" not in login["with"]


def test_every_uses_is_pinned_to_a_full_commit_sha(any_workflow):
    uses = [s["uses"] for _, s in all_steps(any_workflow) if "uses" in s]
    assert uses
    for ref in uses:
        assert SHA_PIN.match(ref), f"{ref} is not pinned to a 40-hex commit SHA"


@pytest.mark.parametrize("name", ["ci.yml", "deploy.yml"])
def test_every_pin_names_its_release_tag_in_a_comment(name):
    for line in (WORKFLOWS / name).read_text(encoding="utf-8").splitlines():
        if "uses:" in line and not line.lstrip().startswith("#"):
            assert re.search(r"@[0-9a-f]{40}\s+# v\d+(\.\d+){2}\s*$", line), line


def test_no_step_echoes_a_secret(any_workflow):
    for job_name, step in all_steps(any_workflow):
        script = str(step.get("run", ""))
        # secrets reach scripts only through env, never interpolated into the script text
        assert not SECRET_EXPRESSION.search(script), f"{job_name}: secret interpolated in run"
        assert "set -x" not in script and "xtrace" not in script
        secret_vars = [
            var
            for var, value in (step.get("env") or {}).items()
            if SECRET_EXPRESSION.search(str(value))
        ]
        for var in secret_vars:
            for line in script.splitlines():
                printing = re.search(r"\b(echo|printf|print|cat|tee|env|printenv)\b", line)
                assert not (printing and re.search(rf"\$\{{?{var}\b", line)), (
                    f"{job_name}: {var} appears on a printing line: {line!r}"
                )
        assert "ACTIONS_STEP_DEBUG" not in (step.get("env") or {})


def test_no_workflow_sets_secrets_at_job_or_workflow_scope_for_all_steps(any_workflow):
    # A secret in workflow-level env would reach every step, including third-party actions.
    assert not SECRET_EXPRESSION.search(str((any_workflow.get("env") or {})))
    for name, job in any_workflow["jobs"].items():
        assert not SECRET_EXPRESSION.search(str(job.get("env") or {})), name


def test_deploy_permissions_default_to_read_only(deploy):
    assert deploy["permissions"] == {"contents": "read"}


def test_ci_is_hardened(ci):
    job = ci["jobs"]["test"]
    assert job["timeout-minutes"] == 90
    assert ci["permissions"] == {"contents": "read"}
    on = triggers(ci)
    assert set(on) == {"push", "pull_request"}
    python = next(s for s in steps(job) if str(s.get("uses", "")).startswith("actions/setup-python@"))
    assert python["with"]["cache"] == "pip"
    ivy = next(s for s in steps(job) if str(s.get("uses", "")).startswith("actions/cache@"))
    assert ivy["with"]["path"] == "~/.ivy2"
    assert "pyproject.toml" in ivy["with"]["key"]


def test_ci_runs_pytest_and_checks_the_wheel_builds(ci):
    runs = [str(s.get("run", "")) for s in steps(ci["jobs"]["test"])]
    assert any(r.strip() == "pytest -q" for r in runs)
    assert any("python -m build --wheel" in r for r in runs)


def test_ci_installs_the_spark_extra(ci):
    runs = [str(s.get("run", "")) for s in steps(ci["jobs"]["test"])]
    assert 'pip install -e ".[dev,spark]"' in runs


def test_ci_hygiene_runs_always_and_fails_on_push_without_the_secret(ci):
    hygiene = next(s for s in steps(ci["jobs"]["test"]) if "check_hygiene.py" in str(s.get("run", "")))
    assert hygiene["if"] == "always()"
    assert hygiene["env"]["HYGIENE_FORBIDDEN"] == "${{ secrets.HYGIENE_FORBIDDEN }}"
    script = hygiene["run"]
    assert '[ -z "$HYGIENE_FORBIDDEN" ]' in script
    assert '"$GITHUB_EVENT_NAME" = "push"' in script
    assert "::error::" in script and "exit 1" in script
    assert "::notice::" in script
    assert script.index("::error::") < script.index("::notice::") < script.index("python tools/check_hygiene.py")


def test_ci_validates_all_terraform_stacks(ci):
    runs = "\n".join(str(s.get("run", "")) for s in steps(ci["jobs"]["test"]))
    assert "terraform fmt -check -recursive infra" in runs
    for stack in (
        "infra/fabric",
        "infra/cost-export",
        "infra/event-hubs",
        "infra/capstone/capacity",
        "infra/capstone/workspace",
    ):
        assert f"terraform -chdir={stack} init -backend=false" in runs
        assert f"terraform -chdir={stack} validate" in runs
    for forbidden in ("terraform plan", "terraform apply"):
        assert forbidden not in runs
    assert ci["jobs"]["test"]["env"]["TF_IN_AUTOMATION"] in (1, "1")
