# ADMCMS MCP Server — Remote-Ready Design

**Datum:** 2026-04-18
**Auteur:** Roland Slagter (Campai) + Claude
**Status:** Design approved — ready for implementation planning

---

## 1. Context & Doel

De huidige [server.py](../../../server.py) is een **lokale stdio MCP server** die Claude Desktop spawnt op de developer's laptop en via `pyodbc` connect naar de Azure SQL replica `ADMCMS-PRODUCTION-REPL01` (CMS Lelystad data).

**Doel:** Dezelfde MCP server tools (`query_database`, `get_schema`, `get_lookups`) beschikbaar maken als een **remote HTTP MCP server** met **Entra ID SSO**, bereikbaar voor:

- Claude Desktop / Claude web / Claude Code CLI (via remote MCP configuratie)
- Interne Campai webapps / tools
- Een externe klant-portal (CMS Lelystad medewerkers in hun eigen Entra tenant)
- Custom AI-agent platforms

**Prioriteit:** alle drie bovenstaande use-cases, zelfde deployment.

## 2. Architecturale Beslissingen (samenvatting)

| Onderwerp | Keuze |
|-----------|-------|
| Transport | HTTP-only (MCP Streamable HTTP); geen stdio mode |
| Hosting | Azure App Service (Linux, Python container) |
| Auth protocol | OAuth 2.1 Resource Server pattern (MCP spec 2025-03-26) |
| Identity provider | Microsoft Entra ID, multi-tenant app registration |
| Authorization | Hybrid allowlist: Entra security group (Campai users) + Key Vault JSON (CMS Lelystad users + exceptions) |
| DB connectie | SQL auth (username/password, **niet roteerbaar** per klant-constraint) — password in Key Vault |
| Vast uitgaand IP | App Service default outbound IPs (meerdere, allowlist bij CMS Lelystad firewall) |
| Secrets | Azure Key Vault + System-Assigned Managed Identity |
| CI/CD | GitHub Actions |
| Infra-as-code | Bicep (`infra/main.bicep`) |
| Git hygiene | Fresh `.git init` (huidige history bevat plaintext password, commit wordt weggegooid) |

## 3. Architectuur

```
Claude Desktop/Web ──HTTPS──┐
Interne webapp      ──HTTPS──┤
Klant-portal        ──HTTPS──┼──▶ Azure App Service (Linux)  ──SQL/TDS──▶ Azure SQL (ADMCMS replica)
AI agents           ──HTTPS──┘    ├─ /mcp                                  (public, firewall op outbound IP)
                                  ├─ /.well-known/oauth-protected-resource
                                  └─ /healthz
                                    │
                                    │ Managed Identity
                                    ▼
                                Azure Key Vault
                                  ├─ admcms-db-password
                                  └─ admcms-allowlist (JSON)

Authorization flow (eenmalig per token lifetime):
Client ──GET /mcp (no token)──▶ Server: 401 + WWW-Authenticate: resource_metadata=...
Client ──GET /.well-known/oauth-protected-resource──▶ Server: returns Entra as authz server
Client ──Auth code flow (browser popup)──▶ Entra: returns JWT
Client ──GET /mcp + Bearer <jwt>──▶ Server: validate JWT → allowlist check → handle MCP request
```

## 4. Project Structuur

```
cms-mcp/
├── src/admcms_mcp/
│   ├── __init__.py
│   ├── server.py          # FastMCP + Starlette wiring, exposes /mcp, /healthz, OAuth metadata
│   ├── auth.py            # JWT validation (Entra JWKS) + hybrid allowlist check
│   ├── db.py              # Connection management, SELECT-only guard, query execution
│   ├── tools.py           # MCP tool handlers: query_database, get_schema, get_lookups
│   ├── schema.py          # SCHEMA_KNOWLEDGE string (verplaatst uit huidige server.py)
│   └── config.py          # Pydantic Settings: env vars + validatie + Key Vault loader
├── tests/
│   ├── test_auth.py
│   ├── test_db.py
│   ├── test_tools.py
│   └── test_integration.py   # optionele live DB test (CI staging only)
├── docs/
│   ├── handleiding.md                # verplaatst uit root
│   ├── deployment.md                 # nieuw: Azure deployment runbook
│   ├── DATABASE-KENNIS/              # ongewijzigd, blijft als referentie
│   └── superpowers/specs/            # dit design doc + toekomstige specs
├── infra/
│   ├── main.bicep                    # App Service Plan, App Service, Key Vault, RBAC
│   ├── main.parameters.json          # dev parameters
│   └── main.parameters.prod.json     # prod parameters
├── .github/workflows/
│   ├── ci.yml                        # lint + test op PR
│   └── deploy.yml                    # build + deploy op merge main
├── Dockerfile                        # Python 3.12-slim + ODBC Driver 18
├── .env.example
├── .gitignore
├── pyproject.toml                    # deps, ruff/black config, Python 3.12+
├── README.md
└── LICENSE
```

## 5. Auth & Authorization

### 5.1 Entra App Registration

- **Naam:** `admcms-mcp`
- **Tenant:** Campai
- **Sign-in audience:** Accounts in any organizational directory (Multi-tenant)
- **Application ID URI:** `api://admcms-mcp` (of de app-GUID)
- **Exposed scope:** `admcms.read`
- **Client types:** Public clients mogen auth-code flow gebruiken (Claude Desktop is een public client zonder client secret). Confidential clients (server-to-server integraties) kunnen ook, met client secret in Key Vault.
- **Redirect URIs:** Claude Desktop gebruikt een vast localhost loopback URI (gedocumenteerd in Anthropic's MCP client config) — deze wordt bij eerste setup toegevoegd aan de app registration. Voor custom clients: redirect URI per client toevoegen of Dynamic Client Registration gebruiken als Entra DCR ondersteunt.

### 5.2 JWT Validatie (`auth.py`)

- JWKS ophalen van `https://login.microsoftonline.com/common/discovery/v2.0/keys`
- Cachen (24u TTL, refresh-on-miss bij onbekende `kid`)
- Per request valideren:
  - Signature (RS256, via JWKS)
  - `exp` niet verstreken
  - `iss` in allowlist van tenant-IDs (Campai tenant + CMS Lelystad tenant; uitbreidbaar via config)
  - `aud` = `api://admcms-mcp`
  - `scp` bevat `admcms.read`
- Library: **`authlib`** (onderhouden, complete, niet zelf schrijven)

### 5.3 Hybrid Allowlist

Allowlist heeft drie lagen, OR-logica (als iemand in één ervan zit → allow):

1. **Tenant allowlist** (harde filter) — JWT `iss` claim moet matchen een van: `campai-tenant-id`, `cms-lelystad-tenant-id`. Als niet → deny zonder verdere checks.
2. **Entra security group** — voor Campai users: JWT `groups` claim bevat group-id `admcms-users` (Entra security group in Campai tenant, beheerd via Entra admin center)
3. **Key Vault JSON** — voor CMS Lelystad users (kunnen we niet in Campai's Entra plaatsen) + losse exceptions / service principals:
   ```json
   {
     "users": ["oid-guid-of-user-1", "oid-guid-of-user-2"],
     "groups": ["optional-extra-group-oid"]
   }
   ```

Check-volgorde in `auth.py`:

```python
def check_access(jwt_claims) -> bool:
    if jwt_claims["iss"] not in TENANT_ALLOWLIST:
        return False
    if CAMPAI_GROUP_ID in jwt_claims.get("groups", []):
        return True
    allowlist = load_keyvault_allowlist()  # cached 5 min
    if jwt_claims["oid"] in allowlist["users"]:
        return True
    if set(jwt_claims.get("groups", [])) & set(allowlist["groups"]):
        return True
    return False
```

Elke allow/deny wordt gelogd met `oid`, `tid`, `upn` (géén volledige token body, privacy-compliant).

### 5.4 OAuth Metadata Endpoint

`GET /.well-known/oauth-protected-resource` retourneert:

```json
{
  "resource": "https://admcms-mcp.azurewebsites.net",
  "authorization_servers": ["https://login.microsoftonline.com/common/v2.0"],
  "scopes_supported": ["admcms.read"],
  "bearer_methods_supported": ["header"]
}
```

Op een MCP request zonder token retourneert de server `401` met header:

```
WWW-Authenticate: Bearer resource_metadata="https://admcms-mcp.azurewebsites.net/.well-known/oauth-protected-resource"
```

Claude Desktop en andere MCP 2025-03-26 clients volgen dit automatisch.

## 6. Database Layer (`db.py`)

### 6.1 Connectie

- SQL auth met credentials uit Key Vault (`admcms-db-username`, `admcms-db-password`)
- `pyodbc` connection string met `ODBC Driver 18 for SQL Server`
- Connection reuse via module-level singleton met health check (idem patroon als huidig, maar thread-safe via lock)
- Timeout: 60s (huidige waarde)
- **Geen wijzigingen aan ADMCMS database zelf** (read-only replica, niet onze database)

### 6.2 SELECT-only Guard

Huidige implementatie blijft grotendeels, maar refactored:

- Regex blocklist van dangerous keywords (INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, EXEC, TRUNCATE, MERGE)
- Eerste niet-witruimte tokens moeten `SELECT`, `WITH` of `--` zijn
- Max 500 rijen, hard limit in SQL (`TOP` injecteren) én in Python (truncate na fetch)

### 6.3 Parameterized Queries (security fix)

Huidige `get_schema(table_name)` gebruikt f-string interpolatie → SQL injection risico. Fix: parameterized query via `cursor.execute(sql, params)`.

## 7. Tools (`tools.py`)

Ongewijzigde functionaliteit t.o.v. huidige server:

- `query_database(sql: str, max_rows: int = 100) -> dict`
- `get_schema(table_name: str) -> dict`
- `get_lookups(table_name: str, column_name: str, top_n: int = 50) -> dict`

Enige wijzigingen: pure functions die `db.py` gebruiken (geen globals), betere type hints, centralized error handling.

## 8. Security & Secrets

### 8.1 Geen secrets in code of environment

- **Azure Key Vault** `kv-admcms-mcp-prod` bevat:
  - `admcms-db-password` (SQL wachtwoord — onveranderd per klant-constraint, maar niet meer in git/code)
  - `admcms-db-username`
  - `admcms-allowlist` (JSON zoals sectie 5.3)
  - `entra-allowed-tenants` (comma-separated tenant-IDs)
- App Service System-Assigned Managed Identity krijgt `Key Vault Secrets User` RBAC role
- Lokaal dev: `DefaultAzureCredential` via `az login` — developer's eigen Entra identity krijgt Key Vault read-access (via RBAC)

### 8.2 Netwerk

- App Service: HTTPS-only, TLS 1.2+, geen HTTP endpoint
- Azure SQL firewall: allowlist bevat App Service outbound IPs (Portal → App Service → Properties → Outbound IP Addresses)
- Bij IP-wijziging (scale/region change): update SQL firewall via CMS Lelystad (manual)
- Geen VNet integration in v1; later optioneel indien compliance dit vereist

### 8.3 Git Hygiene

- **Fresh git init** bij start van refactor (cosmetisch: huidige commit is tuxmanro template boilerplate; password stond alleen in untracked working file, nooit in git history — dus fresh init is preference, niet security-vereist)
- `.gitignore`: `__pycache__/`, `*.pyc`, `.env`, `.env.local`, `.venv/`, `venv/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `files.zip`, `*.egg-info/`
- `.env.example` gecommit (placeholders, geen echte waardes)
- Pre-commit hook (optioneel, niet in v1): `ruff check` + `detect-secrets`

## 9. Local Development

- Python 3.12+ virtualenv (`python -m venv .venv`)
- `pip install -e ".[dev]"` (installeert project editable + dev deps)
- Developer runt `az login` eenmalig → `DefaultAzureCredential` pakt token op
- `uvicorn admcms_mcp.server:app --reload --port 8000` start dev server met auto-reload
- Claude Desktop dev config wijst naar `http://localhost:8000/mcp` (Desktop accepteert localhost zonder TLS)
- Optioneel voor tool-logic debugging: env var `AUTH_DISABLED=true` (standaard uit, expliciet voor dev)

## 10. Testing

| Test file | Coverage focus |
|-----------|----------------|
| `test_auth.py` | JWT validatie (valid/expired/wrong-aud/wrong-iss), allowlist hit/miss, group-claim matching, tenant filter |
| `test_db.py` | SELECT-only guard (alle blocked keywords), parameterized queries, connection retry |
| `test_tools.py` | Tool handlers met mocked `pyodbc.Connection`, error paths |
| `test_integration.py` | Live DB smoke test, alleen in CI staging met service principal |

Coverage targets: `auth.py` en `db.py` >80% (security-kritiek), rest best-effort.

Library: `pytest` + `pytest-asyncio` + `pytest-cov`.

## 11. CI/CD (GitHub Actions)

### 11.1 `.github/workflows/ci.yml` (op PR)

1. Checkout
2. Python 3.12 setup
3. `ruff check` (linting)
4. `ruff format --check` (formatting)
5. `pytest --cov` (unit tests only, geen live DB)
6. `pip-audit` (dependency vulnerability scan)

### 11.2 `.github/workflows/deploy.yml` (op merge naar main)

1. CI steps herhalen
2. Docker build (multi-stage, slim image)
3. Push naar Azure Container Registry
4. `az deployment group create` — Bicep infra updates (idempotent)
5. `az webapp config container set` — nieuwe image deployen
6. Health check op `/healthz`
7. Manual approval gate tussen `dev` en `prod` environment (optioneel: deployment slots als we staging/prod scheiding willen)

GitHub Secrets: `AZURE_CREDENTIALS` (service principal met Contributor op resource group).

## 12. Infrastructure (Bicep)

`infra/main.bicep` provisioneert:

- Resource Group (externally declared, niet door Bicep aangemaakt)
- App Service Plan (Linux, B1 of P1v3 afhankelijk van environment)
- App Service (Python 3.12, container image parameterized)
  - System-Assigned Managed Identity enabled
  - HTTPS-only, TLS 1.2+
  - App Settings: `KEY_VAULT_URI`, `ENTRA_ALLOWED_TENANTS`, `ENTRA_APP_ID`
- Key Vault
  - RBAC authorization mode
  - Secrets toegevoegd via separate `az keyvault secret set` commands (niet in Bicep, vermijdt plaintext in deployment logs)
- Role Assignment: App Service Managed Identity → `Key Vault Secrets User` op Key Vault
- App Insights (optional v1, aangeraden voor logging/monitoring)

`infra/main.parameters.json` voor dev, `main.parameters.prod.json` voor prod. Per nieuwe klant → nieuwe parameters file.

App Registration **niet** in Bicep (handmatig in Entra admin center of via `az ad app create` script, separation of concerns: Entra identity is Campai-tenant-scoped, App Service is resource-group-scoped).

## 13. Migratie Plan (wat verdwijnt / verplaatst / nieuw)

### Verwijderen

- [start_server.bat](../../../start_server.bat) — stdio boot niet meer relevant
- [files.zip](../../../files.zip) — onbekende inhoud, per user bevestiging wegwerpen
- [test_connection.py](../../../test_connection.py) — logica naar `tests/test_db.py`
- `__pycache__/` — gitignored
- Bestaande `.git` folder — fresh init voor schone history (let op: design doc commit wordt in de fresh init opnieuw gemaakt)

### Verplaatsen

- [server.py](../../../server.py) → splitsen over `src/admcms_mcp/{server,tools,db,schema}.py`
- [handleiding_admcms_mcp.md](../../../handleiding_admcms_mcp.md) → `docs/handleiding.md` (+ rewrite voor remote mode)
- [DATABASE-KENNIS/](../../../DATABASE-KENNIS/) → `docs/DATABASE-KENNIS/`

### Nieuw

- `src/admcms_mcp/*.py` (6 modules)
- `tests/*.py` (4 test files)
- `infra/main.bicep`, `infra/main.parameters*.json`
- `.github/workflows/{ci,deploy}.yml`
- `Dockerfile`, `.gitignore`, `.env.example`, `pyproject.toml`, `README.md`
- `docs/deployment.md` (Azure deployment runbook)

### Uitvoer-volgorde

1. **Lokale refactor**: fresh git init, nieuwe structuur, modules splitsen, tests schrijven
2. **Entra setup**: App Registration aanmaken in Campai tenant, scope & multi-tenant config
3. **Azure infra**: Bicep deployen naar dev resource group → Key Vault → secrets erin (DB creds, allowlist)
4. **Lokaal end-to-end**: `uvicorn` local + Claude Desktop wijzen naar localhost, full auth flow testen
5. **GitHub setup**: private repo in Campai org, secrets configureren, eerste CI run
6. **Deploy naar Azure**: merge naar main → GitHub Actions deploy → health check
7. **DB firewall**: App Service outbound IPs verzamelen → aan CMS Lelystad geven voor whitelisting
8. **Remote end-to-end**: Claude Desktop wijzen naar `https://admcms-mcp.azurewebsites.net/mcp`, full flow met SSO testen
9. **Documentatie**: `README.md`, `docs/handleiding.md`, `docs/deployment.md` finaliseren

## 14. Open Items / Out of Scope (v1)

- **VNet integration + private endpoint** — nice-to-have, niet voor v1 tenzij compliance dit vereist
- **Rate limiting per user** — niet in v1; Azure App Service heeft basic DDoS protection, MCP use case is laag volume
- **Audit log naar Log Analytics Workspace** — basic App Insights in v1, structured audit logging later
- **Secondary region failover** — niet in v1 (ADMCMS replica zit in 1 region)
- **Automated Entra group provisioning** — beheer blijft handmatig via Entra admin center
- **Client-specific data filtering** (RLS) — niet in v1; allowlist is voldoende voor huidige use cases

## 15. Assumpties & Risico's

| Item | Risico | Mitigatie |
|------|--------|-----------|
| App Service outbound IPs wisselen | Verbinding breekt tot CMS firewall update | User accepteert; Azure Monitor alert op DB connection failures |
| CMS wijzigt SQL password | Deployment breekt | Password update in Key Vault = 1 CLI commando, geen code change |
| MCP spec verandert (nog in flux) | Breaking changes in client-server contract | Pin MCP SDK versie; test op spec bumps voor upgrade |
| Entra multi-tenant tokens bevatten geen `groups` claim (group overage >200) | Allowlist via group matcht niet | Gebruik Key Vault JSON allowlist als fallback; of configure "always include groups" in app manifest |
| Key Vault rate limits (2000 ops/10s per vault) | Bij high traffic throttling | Cache allowlist 5 min in memory; JWKS 24u TTL |

## 16. Referenties

- [MCP Specification 2025-03-26](https://spec.modelcontextprotocol.io/specification/2025-03-26/)
- [MCP Authorization Spec](https://spec.modelcontextprotocol.io/specification/2025-03-26/basic/authorization/)
- [Microsoft Entra OAuth 2.0 endpoints](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
- [Azure App Service outbound IPs](https://learn.microsoft.com/en-us/azure/app-service/overview-inbound-outbound-ips)
- [authlib documentation](https://docs.authlib.org/)
- [pyodbc + Azure SQL](https://learn.microsoft.com/en-us/azure/azure-sql/database/connect-query-python)
