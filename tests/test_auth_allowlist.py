import json
from unittest.mock import patch

import pytest

from admcms_mcp.auth import _ALLOWLIST_CACHE, Forbidden, check_access


@pytest.fixture(autouse=True)
def clear_allowlist_cache():
    _ALLOWLIST_CACHE["data"] = None
    _ALLOWLIST_CACHE["fetched_at"] = 0.0
    yield


@pytest.fixture
def settings_with_group(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "api://admcms-mcp")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id")
    monkeypatch.setenv("CAMPAI_ALLOWED_GROUP", "campai-group-1")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()


class TestCheckAccess:
    def test_grants_via_campai_group(self, settings_with_group):
        with patch("admcms_mcp.auth.get_secret") as mock_kv:
            mock_kv.return_value = json.dumps({"users": [], "groups": []})
            check_access(
                {
                    "oid": "some-oid",
                    "upn": "user@campai.nl",
                    "groups": ["campai-group-1", "other-group"],
                    "tid": "campai-tenant-id",
                }
            )

    def test_grants_via_user_allowlist(self, settings_with_group):
        with patch("admcms_mcp.auth.get_secret") as mock_kv:
            mock_kv.return_value = json.dumps({"users": ["cms-user-oid"], "groups": []})
            check_access(
                {
                    "oid": "cms-user-oid",
                    "upn": "user@cmslelystad.nl",
                    "groups": [],
                    "tid": "cms-tenant",
                }
            )

    def test_grants_via_group_allowlist(self, settings_with_group):
        with patch("admcms_mcp.auth.get_secret") as mock_kv:
            mock_kv.return_value = json.dumps({"users": [], "groups": ["kv-group-1"]})
            check_access(
                {
                    "oid": "some-oid",
                    "upn": "user@cmslelystad.nl",
                    "groups": ["kv-group-1"],
                    "tid": "cms-tenant",
                }
            )

    def test_denies_when_not_on_any_list(self, settings_with_group):
        with patch("admcms_mcp.auth.get_secret") as mock_kv:
            mock_kv.return_value = json.dumps({"users": [], "groups": []})
            with pytest.raises(Forbidden, match="allowlist"):
                check_access(
                    {
                        "oid": "stranger",
                        "upn": "stranger@example.com",
                        "groups": ["unrelated-group"],
                        "tid": "campai-tenant-id",
                    }
                )

    def test_empty_allowlist_and_no_campai_group_denies(self, settings_with_group):
        with patch("admcms_mcp.auth.get_secret") as mock_kv:
            mock_kv.return_value = ""  # no secret yet
            with pytest.raises(Forbidden):
                check_access(
                    {
                        "oid": "user",
                        "upn": "user@campai.nl",
                        "groups": [],
                        "tid": "campai-tenant-id",
                    }
                )
