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
def settings(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "api://admcms-mcp")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id")
    monkeypatch.delenv("CAMPAI_ALLOWED_GROUP", raising=False)
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()


class TestCheckAccess:
    def test_a_campai_group_grants_through_the_allowlist(self, settings):
        """The Campai group is an allowlist entry like any other.

        It used to have its own shortcut through `CAMPAI_ALLOWED_GROUP`, which
        production never set. Removed; this is the path it actually took.
        """
        with patch("admcms_mcp.auth.get_secret") as mock_kv:
            mock_kv.return_value = json.dumps({"users": [], "groups": ["campai-group-1"]})
            check_access(
                {
                    "oid": "some-oid",
                    "upn": "user@campai.nl",
                    "groups": ["campai-group-1", "other-group"],
                    "tid": "campai-tenant-id",
                }
            )

    def test_grants_via_user_allowlist(self, settings):
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

    def test_grants_via_group_allowlist(self, settings):
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

    def test_denies_when_not_on_any_list(self, settings):
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

    def test_an_empty_allowlist_denies_everyone(self, settings):
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


def test_the_campai_group_shortcut_is_gone(settings):
    """`CAMPAI_ALLOWED_GROUP` was a config path nothing ever set.

    It gave a second way into the same decision, which meant two places to look
    when someone's access was wrong. Setting it must now do nothing at all.
    """
    from admcms_mcp.config import Settings, get_settings

    assert not hasattr(Settings(_env_file=None), "campai_allowed_group")

    import os

    os.environ["CAMPAI_ALLOWED_GROUP"] = "campai-group-1"
    get_settings.cache_clear()
    try:
        with patch("admcms_mcp.auth.get_secret") as mock_kv:
            mock_kv.return_value = json.dumps({"users": [], "groups": []})
            with pytest.raises(Forbidden):
                check_access(
                    {
                        "oid": "some-oid",
                        "upn": "user@campai.nl",
                        "groups": ["campai-group-1"],
                        "tid": "campai-tenant-id",
                    }
                )
    finally:
        os.environ.pop("CAMPAI_ALLOWED_GROUP", None)
        get_settings.cache_clear()
