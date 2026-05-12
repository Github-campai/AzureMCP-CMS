"""Azure Key Vault secret loader.

In production, secrets are fetched via Managed Identity.
In local dev, DefaultAzureCredential falls back to the developer's
`az login` credentials. An env-var override is also supported for
Docker-free local testing without Azure Key Vault access.
"""

import logging
import os
from functools import lru_cache

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

from admcms_mcp.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _client() -> SecretClient:
    settings = get_settings()
    credential = DefaultAzureCredential()
    return SecretClient(vault_url=settings.key_vault_uri, credential=credential)


def _env_override(name: str) -> str | None:
    """Local-dev only: if an env var matching the secret name exists, use it.

    Maps `admcms-db-password` → `ADMCMS_DB_PASSWORD`. Returns None when unset.
    Production deployments never set these env vars — secrets come from Key Vault.
    """
    env_name = name.upper().replace("-", "_")
    return os.environ.get(env_name)


def get_secret(name: str) -> str:
    """Fetch a secret by name from Key Vault, or env var override if set.

    Intentionally uncached: callers decide their own caching policy.
    `auth._load_allowlist` uses a 5-min TTL so revocations take effect;
    `db._get_connection` is called once per process (connection is reused).
    """
    override = _env_override(name)
    if override is not None:
        logger.info("Using env var override for secret %s (local dev)", name)
        return override

    client = _client()
    secret = client.get_secret(name)
    logger.debug("Fetched secret %s from Key Vault", name)
    return secret.value or ""
