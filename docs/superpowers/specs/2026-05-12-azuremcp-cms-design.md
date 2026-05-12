# AzureMCP-CMS — Design Spec

**Datum:** 2026-05-12
**Status:** Approved
**Auteur:** Janneke Post (Campai) + Claude

## Doel

Roland's lokaal-werkende ADMCMS MCP-server (`CMS MCP Server.zip`) deployen naar Azure als
container, zodat klant CMS Lelystad de MCP via hun eigen Entra ID kan gebruiken vanuit Claude,
Copilot Studio en (later) dashboards. Subscription `CMS-MCP-PAYG`
(`1886dd8f-1ce7-4af2-8491-338341544b55`) is van CMS, Campai is MSP en deployt erin.

## Niet-doelen

- Geen inforcer-code of -tools in deze repo. Inforcer dient alleen als deployment-blauwdruk.
- Geen nieuwe MCP-tools: Roland's 20 tools blijven exact zoals ze zijn.
- Geen migratie van Roland's `.git/` historie (start fresh in AzureMCP-CMS).
- Geen On-Behalf-Of dashboard-bridge in V1 (komt later, patroon staat in inforcer).

## Bronnen

| Bron | Rol |
|---|---|
| `CMS MCP Server.zip` (Roland, lokaal getest) | **Payload** — broncode, tools, tests, web UI, docs |
| `Github-campai/AzureMCP-inforcer` | **Blauwdruk** — Bicep, scripts, GitHub Actions pattern |
| `Github-campai/AzureMCP-CMS` | **Target** — bestaat, heeft alleen LICENSE + lege README |

## Doel-architectuur

Alle resources in **één** resource group, region `westeurope`,
naming convention `CMS-MCP-{kind}-prod` (waar Azure lowercase eist: `cms-mcp-...`).

| Resource | Naam |
|---|---|
| Subscription | `CMS-MCP-PAYG` (`1886dd8f-1ce7-4af2-8491-338341544b55`) |
| Resource Group | `CMS-MCP-rg-prod` |
| Container Registry | `cmsmcpacrprod` + 4-char unique suffix |
| Key Vault | `cms-mcp-kv-prod` (lowercase, RBAC, soft-delete) |
| Log Analytics Workspace | `CMS-MCP-law-prod` |
| Application Insights | `CMS-MCP-ai-prod` |
| User-Assigned Managed Identity | `CMS-MCP-id-prod` |
| Container Apps Environment | `CMS-MCP-env-prod` |
| Container App | `cms-mcp-app-prod` (DNS-naam, lowercase) |

Bicep `environmentName` parameter ondersteunt zowel `dev` als `prod`, maar V1 deployt alleen `prod`.

## Auth-architectuur

- Eén **multi-tenant** Entra app registratie `CMS-MCP-API` in Campai tenant
  (`bf030cec-d527-4f3c-9843-db5473bee3f7`).
- Exposed scope `admcms.read`. Geen client secret nodig — server valideert alleen tokens.
- Roland's `auth.py` valideert: signature (JWKS), `exp`, `aud == ENTRA_APP_ID`,
  `tid ∈ ENTRA_ALLOWED_TENANTS`, `iss` matcht tenant, scope `admcms.read`.
- Allowlist (gebruikers/groepen) komt uit Key Vault secret `admcms-allowlist` (JSON).
- DB-wachtwoord komt uit Key Vault secret `admcms-db-password` (Roland's `keyvault.py` doet dit al).
- Container App haalt secrets uit Key Vault via Managed Identity (`Key Vault Secrets User` role).

**App registratie** maakt Janneke later aan met Global Admin login. Script
`scripts/create-entra-app.ps1` is voorbereid maar hoeft niet auto te runnen.

## Klant-onboarding flow (CMS Lelystad)

1. CMS-admin opent admin-consent URL: `https://login.microsoftonline.com/<CMS_TENANT_ID>/adminconsent?client_id=<CMS-MCP-API_CLIENT_ID>`
2. CMS-admin maakt eigen security group `CMS-MCP-Users`, voegt gebruikers toe.
3. Campai voegt `CMS_TENANT_ID` toe aan `ENTRA_ALLOWED_TENANTS` (env var op Container App).
4. Campai voegt het Group OID toe aan KV secret `admcms-allowlist`.
5. CMS-gebruikers gebruiken Claude/Copilot met hun Entra account.

Gedocumenteerd in `docs/ONBOARDING-CUSTOMER.md`.

## Database-toegang

Azure SQL `cmsnl.database.windows.net` / `ADMCMS-PRODUCTION-REPL01` (bestaat al).
SQL-auth: username `saazuredb`, password in Key Vault. Container App outbound IPs
moeten op SQL server firewall whitelist (òf "Allow Azure services").

## Repo-structuur AzureMCP-CMS

```
AzureMCP-CMS/
├── README.md                 # TL;DR + deploy + ops (vervangt huidige lege README)
├── LICENSE                   # bestaande MIT
├── .gitignore, .dockerignore
├── Dockerfile                # uit zip (Python 3.12 + msodbcsql18)
├── pyproject.toml            # uit zip
├── azure.yaml                # azd config
├── src/admcms_mcp/           # Roland's complete src/ 1:1
├── tests/                    # Roland's 60 tests
├── web/                      # admin.html + chat.html
├── docs/
│   ├── DATABASE-KENNIS/      # Roland's datamodel + cookbook
│   ├── handleiding.md, service-handleiding.md
│   ├── DEPLOYMENT.md         # NIEUW
│   ├── ONBOARDING-CUSTOMER.md # NIEUW
│   └── superpowers/          # specs + plans (deze file + nieuwe)
├── infra/                    # NIEUW — Bicep, port van inforcer
│   ├── main.bicep            # subscription scope, maakt RG
│   ├── resources.bicep       # RG scope
│   └── main.parameters.json
├── scripts/                  # NIEUW — port van inforcer (minus inforcer-specifiek)
│   ├── deploy-container-full.ps1
│   ├── deploy-container.ps1
│   ├── seed-keyvault.ps1
│   └── create-entra-app.ps1  # optioneel, Janneke runt later
└── .github/workflows/
    └── deploy-to-aca.yml     # push main = ACR build + ACA update
```

**Uitgesloten uit Roland's zip:** `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `.coverage`,
`__pycache__/`, `cloudflared.exe` (65MB), `data/audit.db`, `.env` (secrets), `.git/`,
`.claude/settings.local.json`.

## CI/CD

- **Auth:** GitHub Actions → Azure via **Federated OIDC** (geen client secret in GH).
  Vereist één AAD app + role assignment op subscription.
- **Image tagging:** zowel `:latest` als `:<git-sha>` (rollback mogelijk).
- **Trigger:** push naar `main` → `az acr build` (cloud-side, geen Docker daemon)
  → `az containerapp update --image`.
- **Bootstrap:** eenmalig `scripts/deploy-container-full.ps1` voor infra + eerste image.

## Implementatie-volgorde

1. Repo-skelet: clone, gitignore/dockerignore, README placeholder.
2. Roland's payload kopiëren (`src/`, `tests/`, `web/`, `docs/`, `Dockerfile`,
   `pyproject.toml`).
3. Bicep schrijven (`infra/main.bicep`, `infra/resources.bicep`).
4. Scripts schrijven (deploy-container-full, deploy-container, seed-keyvault, create-entra-app).
5. GitHub Actions workflow (`deploy-to-aca.yml`).
6. Docs: DEPLOYMENT.md, ONBOARDING-CUSTOMER.md, README.md updaten.
7. Commit + push naar `AzureMCP-CMS` main.
8. (Out of scope V1) actuele Azure deployment uitvoeren — wacht op app registratie door Janneke.

## Open punten (acceptabel voor V1)

- **CMS tenant ID** nog niet bekend. Placeholder in Bicep params, vullen tijdens onboarding.
- **App registratie client ID** komt later (Janneke maakt aan met GA-account).
- **DB firewall regels** bijwerken vereist toegang tot SQL server (`cmsnl.database.windows.net`)
  — wordt gedocumenteerd maar niet automatisch uitgevoerd.

## Verificatie

Na implementatie moet de repo:
- `docker build .` succesvol uitvoeren
- `pytest -v` Roland's 60 tests draaien (lokaal)
- `bicep build infra/main.bicep` valideert zonder errors
- `gh workflow view deploy-to-aca` toont de workflow

Daadwerkelijke Azure-deployment wordt later getriggerd (na app registratie + tenant IDs).
