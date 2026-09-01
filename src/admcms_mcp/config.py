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

    # Authority per hostname, komma-gescheiden als `host=tenant-id`. De authority
    # is het stuk in het pad van de Entra-aanmeldURL, en dat bepaalt in *welke*
    # directory iemand wordt aangemeld. Met "organizations" zoekt Entra de
    # thuistenant van de gebruiker op — voor een gast is dat zijn eigen tenant,
    # niet die waar hij gast is, en dan strandt hij op de tenantcontrole in
    # validate_jwt. Een tenant-ID in het pad meldt hem wel aan in de juiste
    # directory, inclusief zijn groepslidmaatschappen daar.
    #
    # Per hostname en niet globaal: de app antwoordt op een Campai- en een
    # klanthostname, en één authority voor allebei zou de ene partij uit haar
    # eigen tenant duwen. Leeg = overal `entra_authority`, het gedrag van
    # hiervoor.
    entra_authority_by_host: dict[str, str] = {}

    # Extra waarden die als `aud` in een token geaccepteerd worden, naast het
    # app-id en `api://<app-id>`. Komma-gescheiden.
    #
    # Nodig voor Microsoft 365 Copilot: de Teams Developer Portal genereert bij
    # een SSO-registratie een eigen Application ID URI, en het token dat Copilot
    # namens de gebruiker stuurt draagt *die* audience. Zonder dit is elk
    # Copilot-verzoek een 401 op "Wrong audience".
    #
    # Bewust een expliciete lijst en geen patroon: elke waarde hier is een extra
    # publiek dat onze tokens mag dragen, en dat hoort een beheerder een voor
    # een op te schrijven.
    entra_extra_audiences: list[str] = []

    # Rollen per tool (zie roles.py). De rol komt uit de app-roles van Entra.
    role_claim: str = "roles"
    role_default: str = "viewer"
    # "off" | "warn" (alleen loggen, stand tijdens de uitrol) | "enforce"
    role_enforcement: str = "warn"
    # Rollen die gelden bij AUTH_DISABLED=true (alleen lokaal), komma-gescheiden.
    dev_roles: str = "admin"

    # Database connection (non-secret parts; password comes from Key Vault)
    admcms_server: str = "cmsnl.database.windows.net"
    admcms_database: str = "ADMCMS-PRODUCTION-REPL01"
    admcms_driver: str = "ODBC Driver 18 for SQL Server"

    # Log level voor de Azure-SDK's HTTP-logging. De azure-core
    # HttpLoggingPolicy logt op INFO van elke Key Vault- en Table-call de
    # volledige request- en response-headers; dat was ruwweg de helft van het
    # logvolume en maakte echte fouten onvindbaar. "INFO" of "DEBUG" zet het
    # weer aan wanneer je een Azure-call moet uitpluizen.
    azure_sdk_log_level: str = "WARNING"

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

    @field_validator(
        "cors_allow_origins", "public_base_url_aliases", "entra_extra_audiences", mode="before"
    )
    @classmethod
    def split_aliases(cls, v):
        """Split comma-separated string into a list of base URLs."""
        if isinstance(v, str):
            return [a.strip() for a in v.split(",") if a.strip()]
        return v

    @field_validator("entra_authority_by_host", mode="before")
    @classmethod
    def split_authority_by_host(cls, v):
        """Parse `host=authority,host=authority` into a dict, hosts lowercased."""
        if not isinstance(v, str):
            return v
        mapping: dict[str, str] = {}
        for pair in v.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            host, _, authority = pair.partition("=")
            host, authority = host.strip().lower(), authority.strip().strip("/")
            if host and authority:
                mapping[host] = authority
        return mapping

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
