"""Text checks on the Terraform stacks. Nothing here parses HCL or runs Terraform: it reads the
.tf files as text and asserts that no identifier, endpoint or account name is committed."""

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent / "infra"
STACKS = ("fabric", "cost-export", "event-hubs", "capstone/capacity", "capstone/workspace")
CAPSTONE_CAPACITY_NAME = '"capstonef2"'
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
URL = re.compile(r"https?://", re.IGNORECASE)
BLOCK_END = "\n}\n"


def tf_files():
    return sorted(INFRA.rglob("*.tf"))


def stack_text(stack):
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((INFRA / stack).glob("*.tf")))


def resource_block(text, rtype, name):
    """The body of one `resource "<rtype>" "<name>"` block, so a needle is tied to its own resource."""
    header = f'resource "{rtype}" "{name}" {{'
    assert header in text, header
    return text.split(header, 1)[1].split(BLOCK_END, 1)[0]


def sets_default(block):
    """True when a variable block has a `default` attribute (the word in a description does not count)."""
    return re.search(r"^\s*default\s*=", block, re.MULTILINE) is not None


def squash(text):
    """Collapse whitespace so `terraform fmt` column alignment does not matter."""
    return re.sub(r"\s+", " ", text)


def code_lines(path):
    return [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]


def test_all_stacks_exist_with_the_four_files():
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


def test_stacks_pin_their_provider_versions():
    fabric = (INFRA / "fabric" / "versions.tf").read_text(encoding="utf-8")
    assert 'source  = "microsoft/fabric"' in fabric
    assert 'version = "~> 1.14"' in fabric
    cost = (INFRA / "cost-export" / "versions.tf").read_text(encoding="utf-8")
    assert 'source  = "hashicorp/azurerm"' in cost
    assert 'version = "~> 4.0"' in cost
    hubs = (INFRA / "event-hubs" / "versions.tf").read_text(encoding="utf-8")
    assert 'source  = "hashicorp/azurerm"' in hubs
    assert 'version = "~> 4.0"' in hubs
    capacity = (INFRA / "capstone" / "capacity" / "versions.tf").read_text(encoding="utf-8")
    assert 'source  = "hashicorp/azurerm"' in capacity
    assert 'version = "~> 4.0"' in capacity
    workspace = (INFRA / "capstone" / "workspace" / "versions.tf").read_text(encoding="utf-8")
    assert 'source  = "microsoft/fabric"' in workspace
    assert 'version = "~> 1.14"' in workspace


def test_fabric_stack_shape():
    text = squash(stack_text("fabric"))
    assert text.count('resource "fabric_workspace" ') == 2
    assert '"p9-dev"' in text and '"p9-prod"' in text
    assert text.count('identity = { type = "SystemAssigned" }') == 2
    assert "capacity_id" not in "\n".join(
        line for path in (INFRA / "fabric").glob("*.tf") for line in code_lines(path)
    )
    assert "amendment 1" in text
    # The owner creates the workspaces through Azure CLI and is Admin as their creator, so there
    # is no owner role assignment (and no owner_object_id) to manage.
    assert 'role = "Admin"' not in text
    assert "owner_object_id" not in text and "owner_dev" not in text and "owner_prod" not in text
    assert "already Admin" in text
    assert text.count('role = "Contributor"') == 2
    assert text.count("fabric_workspace_role_assignment") == 2
    assert "var.deploy_sp_object_id" in text
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
    for name in ("deploy_sp_object_id",):
        block = fabric.split(f'variable "{name}"', 1)[1].split("\n}\n", 1)[0]
        assert not sets_default(block), name
    for name in ("subscription_id", "storage_account_name"):
        block = cost.split(f'variable "{name}"', 1)[1].split("\n}\n", 1)[0]
        assert not sets_default(block), name
    assert 'default     = "australiaeast"' in cost


def test_cost_export_identifiers_are_sensitive_and_the_reader_is_a_service_principal():
    cost = (INFRA / "cost-export" / "variables.tf").read_text(encoding="utf-8")
    for name in ("subscription_id", "storage_account_name"):
        block = cost.split(f'variable "{name}"', 1)[1].split(BLOCK_END, 1)[0]
        assert squash("sensitive = true") in squash(block), name
    main = (INFRA / "cost-export" / "main.tf").read_text(encoding="utf-8")
    reader = main.split('resource "azurerm_role_assignment" "reader"', 1)[1].split(BLOCK_END, 1)[0]
    assert squash('principal_type = "ServicePrincipal"') in squash(reader)


def test_readme_names_no_owner_object_id():
    readme = (INFRA / "README.md").read_text(encoding="utf-8")
    assert "owner_object_id" not in readme
    assert "already Admin" in readme


def test_gitignore_covers_terraform_local_state_but_not_lock_files():
    ignore = (INFRA.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    for entry in ("*.tfvars", ".terraform/", "*.tfstate*", "*.tfplan"):
        assert entry in ignore
    assert ".terraform.lock.hcl" not in ignore


def test_lock_files_are_committed_next_to_each_stack():
    for stack in STACKS:
        assert (INFRA / stack / ".terraform.lock.hcl").is_file(), stack


def test_event_hubs_stack_shape():
    text = stack_text("event-hubs")
    expected = {
        ("azurerm_resource_group", "event_hubs"): (
            '"rg-p10-event-hubs"',
            'project = "p10"',
        ),
        ("azurerm_eventhub_namespace", "gateway"): (
            'sku = "Standard"',
            "capacity = 1",
            "local_authentication_enabled = false",
            "public_network_access_enabled = true",
            'minimum_tls_version = "1.2"',
            'project = "p10"',
        ),
        ("azurerm_eventhub", "gateway_calls"): (
            'name = "gateway-calls"',
            "partition_count = 1",
            "message_retention = 1",
            "namespace_id = azurerm_eventhub_namespace.gateway.id",
        ),
        ("azurerm_eventhub_consumer_group", "fabric"): (
            'name = "fabric"',
            "eventhub_name = azurerm_eventhub.gateway_calls.name",
        ),
    }
    for (rtype, name), needles in expected.items():
        block = squash(resource_block(text, rtype, name))
        for needle in needles:
            assert needle in block, (rtype, name, needle)


def test_event_hubs_has_one_hub_and_one_consumer_group():
    text = stack_text("event-hubs")
    assert text.count('resource "azurerm_eventhub" ') == 1
    assert text.count('resource "azurerm_eventhub_consumer_group" ') == 1
    assert text.count('resource "azurerm_eventhub_namespace" ') == 1


def test_event_hubs_roles_are_scoped_to_the_hub_for_service_principals():
    main = stack_text("event-hubs")
    sender = main.split('resource "azurerm_role_assignment" "sender"', 1)[1].split(BLOCK_END, 1)[0]
    receiver = main.split('resource "azurerm_role_assignment" "receiver"', 1)[1].split(BLOCK_END, 1)[0]
    assert squash('role_definition_name = "Azure Event Hubs Data Sender"') in squash(sender)
    assert squash('role_definition_name = "Azure Event Hubs Data Receiver"') in squash(receiver)
    assert squash("principal_id = var.sender_principal_id") in squash(sender)
    assert squash("for_each = toset(var.receiver_principal_ids)") in squash(receiver)
    for block in (sender, receiver):
        assert squash("scope = azurerm_eventhub.gateway_calls.id") in squash(block)
        assert squash('principal_type = "ServicePrincipal"') in squash(block)
    assert main.count('resource "azurerm_role_assignment" ') == 2


def test_event_hubs_variables_are_sensitive_without_defaults():
    variables = (INFRA / "event-hubs" / "variables.tf").read_text(encoding="utf-8")
    for name in ("subscription_id", "namespace_name", "sender_principal_id"):
        block = variables.split(f'variable "{name}"', 1)[1].split(BLOCK_END, 1)[0]
        assert not sets_default(block), name
        assert squash("sensitive = true") in squash(block), name
    location = variables.split('variable "location"', 1)[1].split(BLOCK_END, 1)[0]
    assert 'default     = "australiaeast"' in location
    receivers = variables.split('variable "receiver_principal_ids"', 1)[1].split(BLOCK_END, 1)[0]
    assert squash("type = list(string)") in squash(receivers)
    assert squash("default = []") in squash(receivers)


def test_event_hubs_names_come_from_variables_and_no_output_names_them():
    main = stack_text("event-hubs")
    ns = main.split('resource "azurerm_eventhub_namespace"', 1)[1].split(BLOCK_END, 1)[0]
    name_lines = [line for line in ns.splitlines() if re.match(r"\s*name\s*=", line)]
    assert len(name_lines) == 1 and "var.namespace_name" in name_lines[0]
    outputs = "\n".join(code_lines(INFRA / "event-hubs" / "outputs.tf"))
    assert "output " not in outputs
    assert "azurerm_eventhub" not in outputs and "namespace_name" not in outputs


def test_ci_runs_fmt_init_and_validate_for_event_hubs():
    ci = (INFRA.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "terraform -chdir=infra/event-hubs init -backend=false" in ci
    assert "terraform -chdir=infra/event-hubs validate" in ci


def test_readme_describes_the_event_hubs_stack():
    readme = (INFRA / "README.md").read_text(encoding="utf-8")
    assert "`event-hubs/`" in readme
    assert "rg-p10-event-hubs" in readme


# The capstone (Task 6): the F2 is Azure (the personal subscription owner), the workspace is Fabric
# (fabric@). One apply cannot sign in as both (the Azure CLI config directory is process-wide), so
# there are two stacks under infra/capstone/.


def test_capstone_capacity_stack_shape():
    text = stack_text("capstone/capacity")
    expected = {
        ("azurerm_resource_group", "capstone"): (
            'name = "rg-p12-capstone"',
            "location = var.location",
            'project = "capstone"',
        ),
        ("azurerm_fabric_capacity", "capstone"): (
            "name = " + CAPSTONE_CAPACITY_NAME,
            "resource_group_name = azurerm_resource_group.capstone.name",
            "location = azurerm_resource_group.capstone.location",
            "administration_members = [var.capacity_admin_upn]",
            'sku { name = "F2" tier = "Fabric" }',
            'project = "capstone"',
        ),
    }
    for (rtype, name), needles in expected.items():
        block = squash(resource_block(text, rtype, name))
        for needle in needles:
            assert needle in block, (rtype, name, needle)
    assert text.count('resource "azurerm_resource_group" ') == 1
    assert text.count('resource "azurerm_fabric_capacity" ') == 1
    assert text.count("\nresource ") + text.startswith("resource ") == 2


def test_capstone_capacity_is_an_f2_and_no_other_sku():
    block = squash(resource_block(stack_text("capstone/capacity"), "azurerm_fabric_capacity", "capstone"))
    assert re.findall(r'name = "F\d+"', block) == ['name = "F2"']


def test_capstone_workspace_stack_shape():
    text = stack_text("capstone/workspace")
    block = squash(resource_block(text, "fabric_workspace", "capstone"))
    assert 'display_name = "capstone"' in block
    assert "capacity_id = coalesce(var.capacity_id, one(data.fabric_capacity.capstone[*].id))" in block
    lookup = squash(text.split('data "fabric_capacity" "capstone" {', 1)[1].split(BLOCK_END, 1)[0]).strip()
    assert "count = var.capacity_id == null ? 1 : 0" in lookup
    assert "display_name = " + CAPSTONE_CAPACITY_NAME in lookup
    assert text.count('resource "fabric_workspace" ') == 1
    assert text.count("\nresource ") + text.startswith("resource ") == 1


def test_capstone_workspace_requires_an_active_f2():
    text = stack_text("capstone/workspace")
    lookup = squash(text.split('data "fabric_capacity" "capstone" {', 1)[1].split(BLOCK_END, 1)[0])
    assert "lifecycle { postcondition {" in lookup
    assert 'condition = self.sku == "F2" && self.state == "Active"' in lookup
    assert "error_message" in lookup


def test_capstone_workspace_capacity_id_override_is_optional_and_not_a_secret_default():
    variables = (INFRA / "capstone" / "workspace" / "variables.tf").read_text(encoding="utf-8")
    block = squash(variables.split('variable "capacity_id"', 1)[1].split(BLOCK_END, 1)[0])
    assert "type = string" in block
    assert "default = null" in block
    assert "var.capacity_id == null" in block and "trimspace(var.capacity_id)" in block  # validation


def test_capstone_workspace_looks_up_the_capacity_the_other_stack_creates():
    capacity = squash(resource_block(stack_text("capstone/capacity"), "azurerm_fabric_capacity", "capstone"))
    workspace = squash(stack_text("capstone/workspace"))
    assert "name = " + CAPSTONE_CAPACITY_NAME in capacity
    assert "display_name = " + CAPSTONE_CAPACITY_NAME in workspace


def test_capstone_stacks_use_one_provider_each():
    capacity = stack_text("capstone/capacity")
    workspace = stack_text("capstone/workspace")
    assert "microsoft/fabric" not in capacity and 'provider "fabric"' not in capacity
    assert "hashicorp/azurerm" not in workspace and 'provider "azurerm"' not in workspace
    assert "subscription_id = var.subscription_id" in capacity
    assert 'provider "fabric" {}' in workspace


def test_capstone_stacks_have_no_outputs():
    for stack in ("capstone/capacity", "capstone/workspace"):
        outputs = "\n".join(code_lines(INFRA / stack / "outputs.tf"))
        assert "output " not in outputs, stack


def test_capstone_capacity_variables_are_sensitive_without_defaults():
    variables = (INFRA / "capstone" / "capacity" / "variables.tf").read_text(encoding="utf-8")
    for name in ("subscription_id", "capacity_admin_upn"):
        block = variables.split(f'variable "{name}"', 1)[1].split(BLOCK_END, 1)[0]
        assert not sets_default(block), name
        assert squash("sensitive = true") in squash(block), name
    location = variables.split('variable "location"', 1)[1].split(BLOCK_END, 1)[0]
    assert 'default     = "australiaeast"' in location


def test_capstone_location_is_the_f2s_own_region_and_not_empty():
    variables = (INFRA / "capstone" / "capacity" / "variables.tf").read_text(encoding="utf-8")
    location = squash(variables.split('variable "location"', 1)[1].split(BLOCK_END, 1)[0])
    assert "the F2's own region" in location
    assert "may differ from the trial capacity's" in location
    assert "assumption" in location and "start of the live run" in location
    assert "length(trimspace(var.location)) > 0" in location


def test_capstone_commits_no_upn_and_the_example_holds_placeholders():
    for path in (INFRA / "capstone").rglob("*"):
        if path.suffix in (".tf", ".example", ".md"):
            text = path.read_text(encoding="utf-8")
            assert "onmicrosoft" not in text, path
            assert not GUID.search(text), path
    example = (INFRA / "capstone" / "capacity" / "terraform.tfvars.example").read_text(encoding="utf-8")
    for name in ("subscription_id", "capacity_admin_upn"):
        assert re.search(rf'^{name}\s*=\s*"<[^>]+>"', example, re.MULTILINE), name


def test_capstone_readme_documents_the_two_sign_ins():
    readme = (INFRA / "capstone" / "README.md").read_text(encoding="utf-8")
    assert "AZURE_CONFIG_DIR" in readme
    assert "capacity/" in readme and "workspace/" in readme
    assert "rg-p12-capstone" in readme and "F2" in readme
    assert "terraform destroy" in readme


def test_ci_validates_the_capstone_stacks():
    ci = (INFRA.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for stack in ("infra/capstone/capacity", "infra/capstone/workspace"):
        assert f"terraform -chdir={stack} init -backend=false" in ci
        assert f"terraform -chdir={stack} validate" in ci


def test_infra_readme_describes_the_capstone_stacks():
    readme = (INFRA / "README.md").read_text(encoding="utf-8")
    assert "`capstone/`" in readme
    assert "rg-p12-capstone" in readme


def test_capstone_readme_covers_destroy_order_provider_registration_and_the_lookup_checks():
    readme = (INFRA / "capstone" / "README.md").read_text(encoding="utf-8")
    flat = squash(readme)
    assert "az provider register --namespace Microsoft.Fabric" in readme
    assert "az provider show --namespace Microsoft.Fabric" in readme
    assert "destroy `workspace/` before `capacity/`" in flat
    assert "-refresh=false" in readme
    assert "capacity_id" in readme and "postcondition" in readme
    assert "assumption" in flat
