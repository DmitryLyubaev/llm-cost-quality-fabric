import pytest

from verify_deployment_seed import verify_row, verify_rows

MAIN = """
resource "azurerm_cognitive_deployment" "chat" {
  model {
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
  }
  sku {
    name     = "GlobalStandard"
    capacity = var.azure_openai_capacity
  }
}
resource "azurerm_cognitive_deployment" "failover" {
  sku {
    capacity = 1
  }
}
"""
VARIABLES = """
variable "azure_openai_capacity" {
  type        = number
  description = "Capacity, where {braces} in text must not confuse the scanner."
  default     = 300

  validation {
    condition = var.azure_openai_capacity >= 1
  }
}

variable "gpt_model_version" {
  type = string
}

variable "claude_model_version" {
  type    = string
  default = "2"
}

variable "other" {
  type    = number
  default = 7
}
"""
FILES = {
    ("abc123", "infra/bootstrap/openai.tf"): MAIN,
    ("abc123", "infra/bootstrap/variables.tf"): VARIABLES,
}


def git_show(sha, path):
    try:
        return FILES[(sha, path)]
    except KeyError:
        raise RuntimeError(f"no {sha}:{path}") from None


def _row(**overrides):
    row = {
        "account_alias": "rl-primary",
        "deployment_name": "releaselens-chat",
        "model_name": "gpt-4.1-mini",
        "model_version": "2025-04-14",
        "capacity": "300",
        "source": "git",
        "repo": "ReleaseLens",
        "sha": "abc123",
        "path": "infra/bootstrap/openai.tf",
    }
    row.update(overrides)
    return row


def test_matching_row_has_no_problems():
    assert verify_row(_row(), git_show) == []


def test_literal_capacity_in_the_path_file():
    assert verify_row(_row(capacity="1"), git_show) == []


def test_capacity_from_a_variable_default():
    assert verify_row(_row(capacity="300"), git_show) == []


def test_wrong_capacity_is_named():
    problems = verify_row(_row(capacity="500"), git_show)
    assert len(problems) == 1
    assert "rl-primary/releaselens-chat" in problems[0] and "capacity 500" in problems[0]


def test_capacity_default_of_an_unrelated_variable_does_not_count():
    assert verify_row(_row(capacity="7"), git_show)


def test_wrong_version_is_named():
    problems = verify_row(_row(model_version="2024-01-01"), git_show)
    assert len(problems) == 1 and "version 2024-01-01" in problems[0]


def test_wrong_model_name_is_named():
    problems = verify_row(_row(model_name="gpt-5"), git_show)
    assert len(problems) == 1 and "model gpt-5" in problems[0]


def test_version_from_a_variable_default():
    assert verify_row(_row(model_name="gpt-4.1-mini", model_version="2"), git_show) == []


def test_unpinned_requires_a_version_variable_without_default():
    assert verify_row(_row(model_version="unpinned"), git_show) == []
    no_unpinned = dict(FILES)
    no_unpinned[("abc123", "infra/bootstrap/variables.tf")] = VARIABLES.replace(
        'variable "gpt_model_version" {\n  type = string\n}', ""
    )
    problems = verify_row(_row(model_version="unpinned"), lambda s, p: no_unpinned[(s, p)])
    assert len(problems) == 1 and "unpinned" in problems[0]


def test_unreadable_file_is_a_problem():
    problems = verify_row(_row(sha="zzz"), git_show)
    assert len(problems) == 1 and "zzz" in problems[0]


def test_manual_rows_are_skipped():
    row = _row(source="manual", sha=None, repo=None, path=None, capacity="80")
    assert verify_rows([row], {"ReleaseLens": git_show}) == ([], 0)


def test_verify_rows_counts_and_collects():
    rows = [_row(), _row(capacity="500")]
    problems, verified = verify_rows(rows, {"ReleaseLens": git_show})
    assert verified == 1 and len(problems) == 1


def test_unknown_repo_is_a_problem():
    problems, verified = verify_rows([_row(repo="Elsewhere")], {"ReleaseLens": git_show})
    assert verified == 0 and "Elsewhere" in problems[0]


@pytest.mark.parametrize("field", ["model_version", "capacity"])
def test_blank_fields_are_flagged(field):
    assert verify_row(_row(**{field: None}), git_show)
