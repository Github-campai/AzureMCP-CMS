# AzureMCP-CMS

Production MCP server voor de ADMCMS-database (CMS Lelystad). Gehost door Campai
in Azure Container Apps, toegankelijk voor CMS via Entra ID multi-tenant SSO,
bruikbaar vanuit Claude, Copilot Studio, Cursor en VS Code.

## Status

**Live sinds**: 2026-05-12 · **Versie**: V1 (multi-tenant Entra, 20 read-only tools)

| Onderdeel | Waarde |
|---|---|
| **MCP endpoint** | `https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp` |
| Health check | `https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/healthz` |
| OAuth metadata | `https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/.well-known/oauth-protected-resource` |
| Entra App ID | `1e6983d3-f996-4581-9518-e6819b79ac6a` |
| Scope | `admcms.read` |

## Wie doet wat

| Partij | Rol | Verantwoordelijk voor |
|---|---|---|
| **Campai (MSP)** | Hosting + ops | Azure infra, deploys, monitoring, app reg, Key Vault, code-onderhoud |
| **CMS Lelystad (klant)** | Subscription owner + eindgebruikers | Subscription billing, eigen gebruikers/groepen in eigen Entra, admin-consent geven |
| **Roland (Campai dev)** | Tool developer | Functionaliteit van tools, SQL-queries, lokaal testen, PR's naar `main` |

Zie [`docs/OPERATING-MODEL.md`](docs/OPERATING-MODEL.md) voor de verantwoordelijkheden in detail.

## Toegang krijgen

Toegang wordt verleend via Entra security group membership. Twee groups:

| Tenant | Group | Beheerd door | Voor |
|---|---|---|---|
| Campai (`bf030cec-...`) | `SG-Campai-ADMCMS-Users` | Owners: Roland + Janneke | Campai-developers / support |
| CMS Lelystad (`a92e7c27-...`) | `SG-CMS-MCP-Users` | CMS IT-admin | CMS-eindgebruikers |

Lid maken (Campai-zijde): Roland of Janneke in Entra portal → Groups → `SG-Campai-ADMCMS-Users` → Members → + Add. Direct toegang, geen restart of KV-touch nodig.

Lid maken (CMS-zijde): CMS IT-admin in hun eigen Entra portal — zie [`docs/ONBOARDING-CUSTOMER.md`](docs/ONBOARDING-CUSTOMER.md).

## Quick links

- **Roland — hoe test ik dit?** → [`docs/TESTING.md`](docs/TESTING.md)
- **CMS IT-admin — hoe geef ik onze tenant toegang?** → [`docs/ONBOARDING-CUSTOMER.md`](docs/ONBOARDING-CUSTOMER.md)
- **CMS eindgebruiker — hoe gebruik ik dit in Claude (simpel)?** → [`docs/CMS-USER-INSTALL.md`](docs/CMS-USER-INSTALL.md)
- **CMS eindgebruiker — alle clients (Claude, Cursor, VS Code, Copilot Studio)?** → [`docs/CLIENT-SETUP.md`](docs/CLIENT-SETUP.md)
- **Campai ops — hoe deploy/restart/log ik?** → [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)
- **Hoe werkt de code (flows, auth, groepen, tools)?** → [`docs/ARCHITECTUUR.md`](docs/ARCHITECTUUR.md) (levende documentatie)
- **Wat is er recent gewijzigd?** → [`CHANGELOG.md`](CHANGELOG.md)
- **Iemand nieuwe op een MCP-project — hoe is dit gebouwd?** → [`docs/superpowers/specs/2026-05-12-azuremcp-cms-design.md`](docs/superpowers/specs/2026-05-12-azuremcp-cms-design.md)

## Wat doet dit?

23 read-only datatools op de ADMCMS database (Azure SQL `cmsnl`), plus
`describe_server` en `health_check` (25 tools totaal). Voorbeelden van vragen die een AI-assistent kan
beantwoorden:

- "Wat zijn onze top 10 klanten dit jaar versus vorig jaar?"
- "Welke producten hebben verdachte prijzen?"
- "Wat is de marge per productgroep voor B2B?"
- "Toon recente orders van klant X met afwijkende verzendkosten."

De AI roept de juiste tool aan (`get_top_customers`, `detect_price_anomalies`,
`get_margin_report`, `get_customer_orders`, ...), de server vertaalt naar SQL,
parametriseert, valideert SELECT-only, en geeft JSON terug. Elke call wordt
geaudit met user, query, duur.

## Tools (25)

| Categorie | Tools |
|---|---|
| Meta / diagnose | `describe_server`, `health_check` |
| Schema | `query_database`, `get_schema`, `get_lookups`, `get_filter_options` |
| Producten / voorraad | `search_products`, `get_stock_levels`, `get_dead_stock`, `get_supplier_purchases` |
| Omzet | `get_revenue`, `get_revenue_by_business_unit`, `get_margin_report`, `get_kpi_summary`, `get_top_customers`, `get_country_segment_matrix` |
| Logistiek | `get_shipping_summary`, `get_shipping_cost_analysis` |
| Klanten/orders | `get_customer_orders`, `detect_price_anomalies` |
| Financieel | `get_credit_note_analysis`, `get_payment_method_stats` |
| Sales | `get_salesrep_performance`, `get_discount_analysis`, `get_order_origin_mix` |

Allemaal SELECT-only, parameterized, met SQL identifier validation en audit log.
Volledige beschrijvingen (doel, args, tabellen, filters, paginatie) in
[`docs/ARCHITECTUUR.md`](docs/ARCHITECTUUR.md#6-tools--wat-er-is-en-wat-ze-doen), live via `/admin/tools`
(Bearer JWT vereist) of in [`docs/DATABASE-KENNIS/ADMCMS_Query_Cookbook.md`](docs/DATABASE-KENNIS/ADMCMS_Query_Cookbook.md).

## Endpoints

| Path | Auth | Doel |
|---|---|---|
| `/healthz` | public | Health check |
| `/.well-known/oauth-protected-resource[/mcp]` | public | RFC 9728 resource metadata |
| `/.well-known/oauth-authorization-server[/mcp]` | public | RFC 8414 authorization-server metadata |
| `/register` | public | RFC 7591 dynamic client registration (stub) |
| `/authorize` | public | Redirect naar Entra, scope herschreven |
| `/token` | public | Proxy van de code/refresh-exchange naar Entra |
| `/mcp` | Bearer JWT | MCP Streamable HTTP endpoint |
| `/admin/tools` | Bearer JWT | Tool registry incl. input schemas |
| `/admin/audit` | Bearer JWT | Audit log entries (filters: `tool`, `limit`, `since`, `errors_only`) |
| `/admin/audit/{id}` | Bearer JWT | Volledige audit entry incl. SQL queries |
| `/admin/stats` | Bearer JWT | Per-tool call counts, avg duration, error counts (laatste 7d) |
| `/admin/health` | Bearer JWT | Audit-store status + uptime |

## Azure resources

Alle resources in resource group `CMS-MCP-rg-prod` in `westeurope`:

| Resource | Naam |
|---|---|
| Subscription | `CMS-MCP-PAYG` (`1886dd8f-1ce7-4af2-8491-338341544b55`) |
| Resource Group | `CMS-MCP-rg-prod` |
| Container App | `cms-mcp-app-prod` |
| Container Registry | `cmsmcpacrprodcr3e` |
| Key Vault | `cms-mcp-kv-prod` |
| Managed Identity | `CMS-MCP-id-prod` |
| Application Insights | `CMS-MCP-ai-prod` |
| Log Analytics | `CMS-MCP-law-prod` |
| Container Apps Env | `CMS-MCP-env-prod` |

DB: Azure SQL `cmsnl.database.windows.net` / `ADMCMS-PRODUCTION-REPL01` (bestaat al
in subscription `176aa320-3340-49ca-ae1d-019dffd44bf8` / RG `mssql-rep`).

## Lokaal ontwikkelen

```powershell
git clone https://github.com/Github-campai/AzureMCP-CMS.git
cd AzureMCP-CMS
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
# Edit .env: AUTH_DISABLED=true voor tool-logic debugging zonder Entra
uvicorn admcms_mcp.server:app --port 8000
```

Test: `curl http://localhost:8000/healthz`.

Tests: `pytest -v --cov=admcms_mcp` (442 tests). Lint: `ruff check . && ruff format --check .`
Beide draaien in CI (`.github/workflows/ci.yml`) op elke push en PR.

## CI/CD

Push naar `main` triggert GitHub Actions (`.github/workflows/deploy-to-aca.yml`) →
`az acr build` (cloud-side, geen Docker daemon nodig) → `az containerapp update`.
End-to-end ~3 min. Federated OIDC, geen secrets in workflow yaml.

Daarnaast draaien op elke push en PR:
- `ci.yml` — ruff, pytest, pip-audit, plus een import-check met de daadwerkelijk
  geresolveerde dependency-set
- `security-scan.yml` — Gitleaks, Trivy, Semgrep (ook elke nacht)

Dependency-ranges hebben een bovengrens per major. Dat is niet cosmetisch:
`mcp` 2.0 verwijderde `mcp.server.fastmcp`, dus een ongebonden range bouwde een
image die op import crashte.

## Origine

- **Code**: door Roland geschreven en lokaal getest (april 2026).
- **Deployment-patroon**: gebaseerd op [AzureMCP-inforcer](https://github.com/Github-campai/AzureMCP-inforcer)
  (Campai MSP MCP-server voor Inforcer), aangepast naar Container Apps in plaats
  van App Service.
- **Customer template**: [Customer_MCPContainer](https://github.com/Github-campai/Customer_MCPContainer)
  diende als referentie voor het multi-tenant MSP-patroon.

## License

MIT — zie [`LICENSE`](LICENSE).
