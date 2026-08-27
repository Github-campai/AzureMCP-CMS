"""The bicep must describe the audit storage that production actually uses.

Azure had the storage account, the table and the role assignment; the bicep did
not. Nothing was broken until the next `az deployment group create`, which would
have reset the app to AUDIT_STORE=local — back to a SQLite file in the
container's /tmp, split across replicas and wiped by every restart.

These read the template as text on purpose: no Azure, no bicep CLI, and they
fail on exactly the drift that made the trail disappear.
"""

from __future__ import annotations

from pathlib import Path

import pytest

BICEP = Path(__file__).resolve().parents[1] / "infra" / "resources.bicep"


@pytest.fixture(scope="module")
def template() -> str:
    return BICEP.read_text(encoding="utf-8")


def test_the_container_app_is_pointed_at_table_storage(template: str):
    block = template.split("name: 'AUDIT_STORE'", 1)[1][:200]
    assert "'azure_table'" in block
    assert "'local'" not in block


def test_no_audit_database_on_the_container_filesystem(template: str):
    assert "/tmp/audit.db" not in template, "a path under /tmp is per-replica and gone on restart"


def test_the_account_url_is_derived_from_the_declared_account(template: str):
    block = template.split("name: 'AUDIT_TABLE_ACCOUNT_URL'", 1)[1][:250]
    assert "auditStorage.name" in block, "hard-coding the URL lets it drift from the resource"


def test_the_storage_account_and_table_are_declared(template: str):
    assert "resource auditStorage 'Microsoft.Storage/storageAccounts@" in template
    assert (
        "resource auditTable 'Microsoft.Storage/storageAccounts/tableServices/tables@" in template
    )


def test_the_account_name_is_a_parameter_so_a_redeploy_binds_to_the_existing_one(
    template: str,
):
    assert "param auditStorageAccountName string = 'cmsmcpauditprod39b6'" in template


def test_the_identity_may_write_the_table(template: str):
    assert "storageTableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'" in template
    assert "resource auditTableRoleForIdentity" in template
    assert (
        "principalId: managedIdentity.properties.principalId"
        in template.split("resource auditTableRoleForIdentity", 1)[1][:600]
    )


def test_shared_key_access_is_off(template: str):
    """The app authenticates with its managed identity; a key would only be a liability."""
    block = template.split("resource auditStorage", 1)[1].split("resource auditTableService", 1)[0]
    assert "allowSharedKeyAccess: false" in block


def test_retention_is_configured_rather_than_implied(template: str):
    assert "param auditRetentionDays int = 365" in template
    assert "name: 'AUDIT_RETENTION_DAYS'" in template
