from unittest.mock import patch

import pytest

from admcms_mcp.auth import InvalidToken, validate_jwt


@pytest.fixture
def jwks_patch(rsa_key):
    """Patch auth._get_key_for_kid to return our test public key."""
    with patch("admcms_mcp.auth._get_key_for_kid") as mock:
        mock.return_value = rsa_key["public_pem"]
        yield mock


@pytest.fixture
def settings_patch(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "api://admcms-mcp")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id,cms-lelystad-tenant-id")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()


class TestValidateJwt:
    def test_valid_token_returns_claims(self, make_token, jwks_patch, settings_patch):
        token = make_token()
        claims = validate_jwt(token)
        assert claims["oid"] == "user-oid-1"
        assert claims["tid"] == "campai-tenant-id"

    def test_expired_token_rejected(self, make_token, jwks_patch, settings_patch):
        token = make_token(exp_offset=-60)
        with pytest.raises(InvalidToken, match="expired"):
            validate_jwt(token)

    def test_wrong_audience_rejected(self, make_token, jwks_patch, settings_patch):
        token = make_token(aud="api://some-other-app")
        with pytest.raises(InvalidToken, match="audience"):
            validate_jwt(token)

    def test_audience_accepts_both_v1_and_v2_forms(self, make_token, jwks_patch, monkeypatch):
        """ENTRA_APP_ID set as bare GUID must accept tokens with api://<guid> audience."""
        monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
        monkeypatch.setenv("ENTRA_APP_ID", "admcms-mcp")
        monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id,cms-lelystad-tenant-id")
        from admcms_mcp.config import get_settings

        get_settings.cache_clear()
        # v1-style token with api:// prefix
        token_v1 = make_token(aud="api://admcms-mcp")
        assert validate_jwt(token_v1)["oid"] == "user-oid-1"
        # v2-style token with bare GUID
        token_v2 = make_token(aud="admcms-mcp")
        assert validate_jwt(token_v2)["oid"] == "user-oid-1"

    def test_untrusted_tenant_rejected(self, make_token, jwks_patch, settings_patch):
        token = make_token(
            iss="https://login.microsoftonline.com/hostile-tenant/v2.0",
            tid="hostile-tenant",
        )
        with pytest.raises(InvalidToken, match="tenant|issuer"):
            validate_jwt(token)

    def test_missing_scope_rejected(self, make_token, jwks_patch, settings_patch):
        token = make_token(scp="wrong.scope")
        with pytest.raises(InvalidToken, match="scope"):
            validate_jwt(token)

    def test_app_role_token_accepted(self, make_token, jwks_patch, settings_patch):
        """Client-credentials token: no scp, admcms.invoke in roles → accepted."""
        token = make_token(scp="", roles=["admcms.invoke"], oid="sp-oid-1")
        claims = validate_jwt(token)
        assert claims["oid"] == "sp-oid-1"
        assert claims["roles"] == ["admcms.invoke"]

    def test_app_token_without_role_rejected(self, make_token, jwks_patch, settings_patch):
        """Client-credentials token with no scp and no matching role → rejected."""
        token = make_token(scp="", roles=[])
        with pytest.raises(InvalidToken, match="scope|role"):
            validate_jwt(token)

    def test_malformed_token_rejected(self, jwks_patch, settings_patch):
        with pytest.raises(InvalidToken):
            validate_jwt("not.a.jwt")

    def test_scp_null_in_payload_rejected_cleanly(self, jwks_patch, settings_patch, rsa_key):
        """JWT with `scp: null` in payload must raise InvalidToken, not TypeError."""
        import time

        from authlib.jose import jwt

        payload = {
            "iss": "https://login.microsoftonline.com/campai-tenant-id/v2.0",
            "aud": "api://admcms-mcp",
            "oid": "user-oid-1",
            "tid": "campai-tenant-id",
            "upn": "roland@campai.nl",
            "scp": None,
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
        }
        header = {"alg": "RS256", "kid": rsa_key["kid"]}
        token = jwt.encode(header, payload, rsa_key["private_pem"])
        token_str = token.decode() if isinstance(token, bytes) else token

        with pytest.raises(InvalidToken, match="scope"):
            validate_jwt(token_str)

    def test_issuer_prefix_attack_rejected(self, make_token, jwks_patch, settings_patch):
        """Issuer must match exact /v1.0 or /v2.0 form, not a prefix."""
        token = make_token(
            iss="https://login.microsoftonline.com/campai-tenant-id.attacker.example/v2.0",
        )
        with pytest.raises(InvalidToken, match="[Ii]ssuer"):
            validate_jwt(token)

    def test_issuer_without_version_suffix_rejected(self, make_token, jwks_patch, settings_patch):
        """Issuer missing the /v1.0 or /v2.0 suffix must be rejected."""
        token = make_token(iss="https://login.microsoftonline.com/campai-tenant-id")
        with pytest.raises(InvalidToken, match="[Ii]ssuer"):
            validate_jwt(token)
