# AzureMCP-CMS

Production MCP server exposing the ADMCMS (CMS Lelystad automotive parts) database
to AI agents — hosted by Campai on Azure Container Apps, accessed by CMS users
via Entra ID multi-tenant SSO.

## TL;DR

**Wat doet dit?**
Een MCP-server die 20 read-only tools blootstelt op de ADMCMS database (Azure SQL).
AI-tools (Claude, Copilot Studio, Cursor) kunnen vragen stellen als "wat zijn de
top klanten dit jaar?", "wat is de marge per segment?", "welke producten hebben
verdachte prijzen?" — de server vertaalt die naar SQL en geeft het antwoord terug.

**Hoe werkt het?**
De server draait als Docker container in **Azure Container Apps** in subscription
`CMS-MCP-PAYG` (van CMS, gedeployd door Campai als MSP). Toegang via **Entra ID
OAuth** met multi-tenant app registratie: CMS-gebruikers loggen in met hun eigen
account in hun eigen tenant, server valideert token + allowlist, dan pas tool-call.

**Iets aanpassen?**
Wijzig code in `src/admcms_mcp/`, push naar `main` — GitHub Actions bouwt nieuwe
image in ACR en update Container App. ~3 minuten tot live.

**Iets stuk?**
```powershell
az containerapp logs show --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod --follow
```
Application Insights: `CMS-MCP-ai-prod` in Azure Portal.

## Deployment

| Resource | Naam |
|---|---|
| Subscription | `CMS-MCP-PAYG` (`1886dd8f-1ce7-4af2-8491-338341544b55`) |
| Resource Group | `CMS-MCP-rg-prod` (westeurope) |
| Container App | `cms-mcp-app-prod` |
| Container Registry | `cmsmcpacrprod<token>` |
| Key Vault | `cms-mcp-kv-prod` |

Volledige procedure: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).
Klant-onboarding: [`docs/ONBOARDING-CUSTOMER.md`](docs/ONBOARDING-CUSTOMER.md).
Originele design spec: [`docs/superpowers/specs/2026-05-12-azuremcp-cms-design.md`](docs/superpowers/specs/2026-05-12-azuremcp-cms-design.md).

## Tools (20)

- `query_database`, `get_schema`, `get_lookups`
- `search_products`, `get_stock_levels`, `get_supplier_purchases`
- `get_revenue`, `get_revenue_by_business_unit`, `get_margin_report`
- `get_kpi_summary`, `get_top_customers`
- `get_shipping_summary`, `get_shipping_cost_analysis`
- `get_customer_orders`, `detect_price_anomalies`
- `get_credit_note_analysis`, `get_payment_method_stats`
- `get_salesrep_performance`, `get_discount_analysis`
- `get_order_origin_mix`

Allemaal SELECT-only, parameterized, met SQL identifier validation en audit log.

## Endpoints

| Path | Auth | Doel |
|---|---|---|
| `/healthz` | public | Health check |
| `/.well-known/oauth-protected-resource` | public | OAuth 2.1 Resource Server metadata (MCP spec) |
| `/mcp` | Bearer JWT | MCP Streamable HTTP endpoint |
| `/admin/tools` | Bearer JWT | Tool registry |
| `/admin/audit` | Bearer JWT | Audit log entries |
| `/admin/stats` | Bearer JWT | Per-tool stats (7d) |

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
# Edit .env: set AUTH_DISABLED=true for tool-logic debugging
uvicorn admcms_mcp.server:app --port 8000
```

Test: `curl http://localhost:8000/healthz`.

Tests: `pytest -v --cov=admcms_mcp` (60 tests, ~70% coverage).

## Origine

- **Code**: door Roland geschreven en lokaal getest (april 2026). Repo: privé zip.
- **Deployment-patroon**: gebaseerd op [AzureMCP-inforcer](https://github.com/Github-campai/AzureMCP-inforcer)
  (Campai MSP MCP-server voor Inforcer), aangepast naar Container Apps in plaats
  van App Service.
- **Customer template**: [Customer_MCPContainer](https://github.com/Github-campai/Customer_MCPContainer)
  diende als referentie voor het multi-tenant MSP-patroon.

## License

MIT — zie [`LICENSE`](LICENSE).
