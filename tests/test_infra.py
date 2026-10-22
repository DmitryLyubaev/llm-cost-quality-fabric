"""Text checks on the Terraform stacks. Nothing here parses HCL or runs Terraform: it reads the
.tf files as text and asserts that no identifier, endpoint or account name is committed."""

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent / "infra"
STACKS = ("fabric", "cost-export")
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
URL = re.compile(r"https?://", re.IGNORECASE)


def tf_files():
    return sorted(INFRA.rglob("*.tf"))


def stack_text(stack):
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((INFRA / stack).glob("*.tf")))


def squash(text):
    """Collapse whitespace so `terraform fmt` column alignment does not matter."""
    return re.sub(r"\s+", " ", text)


def code_lines(path):
    return [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]


def test_both_stacks_exist_with_the_four_files():
    for stack in STACKS:
        for name in ("main.tf", "variables.tf", "outputs.tf", "versions.tf"):
            assert (INFRA / stack / name).is_file(), f"{stack}/{name}"
    assert (INFRA / "README.md").is_file()


def test_no_guid_or_url_in_any_tf_file():
    files = tf_files()
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not GUID.search(text), f"GUID in {path}"
        assert not URL.search(text), f"URL in {path}"


def test_no_literal_storage_account_name():
    main = (INFRA / "cost-export" / "main.tf").read_text(encoding="utf-8")
    block = main.split('resource "azurerm_storage_account"', 1)[1].split("\n}\n", 1)[0]
    name_lines = [line for line in block.splitlines() if re.match(r"\s*name\s*=", line)]
    assert len(name_lines) == 1
    assert "var.storage_account_name" in name_lines[0]
    for path in tf_files():
        for line in code_lines(path):
            if "storage_account_name" in line:
                assert "var." in line or line.lstrip().startswith("variable"), f"{path}: {line}"


def test_both_stacks_pin_their_provider_versions():
    fabric = (INFRA / "fabric" / "versions.tf").read_text(encoding="utf-8")
    assert 'source  = "microsoft/fabric"' in fabric
    assert 'version = "~> 1.14"' in fabric
    cost = (INFRA / "cost-export" / "versions.tf").read_text(encoding="utf-8")
    assert 'source  = "hashicorp/azurerm"' in cost
    assert 'version = "~> 4.0"' in cost


def test_fabric_stack_shape():
    text = squash(stack_text("fabric"))
    assert text.count('resource "fabric_workspace" ') == 2
    assert '"p9-dev"' in text and '"p9-prod"' in text
    assert text.count('identity = { type = "SystemAssigned" }') == 2
    assert "capacity_id" not in "\n".join(
        line for path in (INFRA / "fabric").glob("*.tf") for line in code_lines(path)
    )
    assert "amendment 1" in text
    assert 'role = "Admin"' in text and 'role = "Contributor"' in text
    assert "var.owner_object_id" in text and "var.deploy_sp_object_id" in text
    assert text.count("service_principal_id") >= 2


def test_cost_export_stack_shape():
    text = squash(stack_text("cost-export"))
    for needle in (
        "is_hns_enabled                  = true",
        "shared_access_key_enabled       = false",
        "public_network_access_enabled   = true",
        'min_tls_version                 = "TLS1_2"',
        "allow_nested_items_to_be_public = false",
        'account_tier             = "Standard"',
        'account_replication_type = "LRS"',
        'account_kind             = "StorageV2"',
        '"cost-exports"',
        '"rg-p9-cost-export"',
        'project = "p9"',
        "Storage Blob Data Reader",
        "for_each             = toset(var.reader_principal_ids)",
        "storage_use_azuread = true",
    ):
        assert squash(needle) in text, needle
    assert "amendment 6" in text


def test_cost_export_has_no_output_naming_the_account():
    outputs = (INFRA / "cost-export" / "outputs.tf").read_text(encoding="utf-8")
    assert "azurerm_storage_account" not in outputs
    assert "storage_account_name" not in outputs


def test_variables_have_no_secret_defaults():
    fabric = (INFRA / "fabric" / "variables.tf").read_text(encoding="utf-8")
    cost = (INFRA / "cost-export" / "variables.tf").read_text(encoding="utf-8")
    for name in ("owner_object_id", "deploy_sp_object_id"):
        block = fabric.split(f'variable "{name}"', 1)[1].split("\n}\n", 1)[0]
        assert "default" not in block
    for name in ("subscription_id", "storage_account_name"):
        block = cost.split(f'variable "{name}"', 1)[1].split("\n}\n", 1)[0]
        assert "default" not in block
    assert 'default     = "australiaeast"' in cost


def test_gitignore_covers_terraform_local_state_but_not_lock_files():
    ignore = (INFRA.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    for entry in ("*.tfvars", ".terraform/", "*.tfstate*", "*.tfplan"):
        assert entry in ignore
    assert ".terraform.lock.hcl" not in ignore


def test_lock_files_are_committed_next_to_each_stack():
    for stack in STACKS:
        assert (INFRA / stack / ".terraform.lock.hcl").is_file(), stack
