import pytest
from pydantic import ValidationError

from admcms_mcp.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv-test.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "app-id-123")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "tenant-a,tenant-b")
    monkeypatch.setenv("ADMCMS_SERVER", "test.db")

    s = Settings()

    assert s.key_vault_uri == "https://kv-test.vault.azure.net/"
    assert s.entra_app_id == "app-id-123"
    assert s.entra_allowed_tenants == ["tenant-a", "tenant-b"]
    assert s.admcms_server == "test.db"
    assert s.auth_disabled is False


def test_settings_auth_disabled_from_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "id")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "t")
    monkeypatch.setenv("AUTH_DISABLED", "true")

    s = Settings()
    assert s.auth_disabled is True


def test_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    monkeypatch.delenv("ENTRA_APP_ID", raising=False)
    monkeypatch.delenv("ENTRA_ALLOWED_TENANTS", raising=False)

    with pytest.raises(ValidationError):
        Settings()
