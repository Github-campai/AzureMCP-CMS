"""Typed settings loaded from environment variables.

In production, secrets referenced here (DB password, allowlist JSON) are
loaded from Azure Key Vault at runtime via `keyvault.py`, not from env.
Only non-secret config lives here.
"""

from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import EnvSettingsSource


class EnvSettingsSourceNoJson(EnvSettingsSource):
    """Custom env source that treats all values as strings (no JSON parsing)."""

    def prepare_field_value(
        self, field_name: str, field, value: Any, value_is_complex: bool
    ) -> Any:
        """Override to skip JSON parsing for complex types."""
        # Always return the raw value for env vars; let validators handle parsing
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Azure Key Vault — where secrets come from in production
    key_vault_uri: str

    # Entra app registration identifiers
    entra_app_id: str
    entra_allowed_tenants: list[str]
    campai_allowed_group: str = ""  # optional; empty disables group check

    # Authority segment the OAuth proxy sends users to. "organizations" matches
    # this app's AzureADMultipleOrgs audience: any work/school tenant may sign
    # in, and validate_jwt still rejects tenants outside entra_allowed_tenants.
    entra_authority: str = "organizations"

    # Externally-visible base URL, e.g. https://admcms.campai.nl. Leave empty to
    # derive it from the ingress' X-Forwarded-* headers. Set it as soon as a
    # custom domain fronts the app, so the published OAuth metadata names that
    # domain instead of the Container Apps hostname.
    public_base_url: str = ""

    # Extra hostnames waarop de app antwoordt, komma-gescheiden. Elke bekende
    # hostname beschrijft zichzelf in de OAuth-metadata; onbekende hosts vallen
    # terug op public_base_url zodat een gespoofte Host-header geen vreemd
    # domein kan laten adverteren.
    public_base_url_aliases: list[str] = []

    # Extra origins die CORS mag gebruiken, komma-gescheiden. Leeg = afgeleid
    # van public_base_url + aliases + "null" (de admin-UI over file://). Zet
    # "*" om terug te vallen op het oude gedrag als een browserclient breekt.
    cors_allow_origins: list[str] = []

    # Database connection (non-secret parts; password comes from Key Vault)
    admcms_server: str = "cmsnl.database.windows.net"
    admcms_database: str = "ADMCMS-PRODUCTION-REPL01"
    admcms_driver: str = "ODBC Driver 18 for SQL Server"

    # Escape hatches (development only)
    auth_disabled: bool = False

    # Escape hatch for Copilot Studio's preview MCP connector, which used to
    # send unauthenticated GET probes to /mcp after a successful authenticated
    # POST. Off by default: an unauthenticated GET otherwise holds an SSE stream
    # open on a 0.5-vCPU app. Flip to true (env var, no redeploy) only if a
    # client regresses.
    allow_unauthenticated_mcp_get: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """Use custom env source that doesn't JSON-parse list types."""
        return (
            init_settings,
            EnvSettingsSourceNoJson(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    @field_validator("cors_allow_origins", "public_base_url_aliases", mode="before")
    @classmethod
    def split_aliases(cls, v):
        """Split comma-separated string into a list of base URLs."""
        if isinstance(v, str):
            return [a.strip() for a in v.split(",") if a.strip()]
        return v

    @field_validator("entra_allowed_tenants", mode="before")
    @classmethod
    def split_tenants(cls, v):
        """Split comma-separated string into list of tenants."""
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
