# Testing — voor Roland

Twee manieren om te testen: **lokaal** (snelle iteratie, geen Azure nodig) of
**tegen de live Azure deployment** (met echte Entra auth).

## Live MCP server (Azure)

| | |
|---|---|
| URL | `https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io` |
| MCP endpoint | `/mcp` |
| Entra App ID (audience) | `1e6983d3-f996-4581-9518-e6819b79ac6a` |
| Scope | `admcms.read` |
| Resource URI voor tokens | `api://1e6983d3-f996-4581-9518-e6819b79ac6a` |

## Toegang krijgen (eenmalig)

Jouw Campai-account moet op de allowlist staan in Key Vault. Vraag Janneke om je
Entra OID toe te voegen aan secret `admcms-allowlist` in Key Vault `cms-mcp-kv-prod`.

Je OID vind je met:

```powershell
az login
az ad signed-in-user show --query id -o tsv
```

## Optie 1 — Snelle smoke test met curl

```powershell
az login
$token = az account get-access-token --resource api://1e6983d3-f996-4581-9518-e6819b79ac6a --query accessToken -o tsv

# Lijst van alle tools (auth-check + tool registry)
curl -H "Authorization: Bearer $token" https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/admin/tools | jq

# Een tool aanroepen via MCP protocol — schema van Customer-tabel
$body = @{
    jsonrpc = "2.0"
    id = 1
    method = "tools/call"
    params = @{
        name = "get_schema"
        arguments = @{ table = "Customer" }
    }
} | ConvertTo-Json -Depth 10 -Compress

curl -X POST `
    -H "Authorization: Bearer $token" `
    -H "Content-Type: application/json" `
    -d $body `
    https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp
```

Krijg je `200` met een tool response → MCP + Entra + DB werken allemaal.
Krijg je `401` → token expired of niet op allowlist.
Krijg je `500` met DB error → check de logs (zie onder).

## Optie 2 — Claude Desktop / Claude Code (echte AI-gebruik)

Voeg toe aan je MCP config (`~/.claude/mcp_settings.json` of via `claude mcp add`):

```json
{
  "mcpServers": {
    "admcms": {
      "url": "https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp",
      "transport": "http"
    }
  }
}
```

Claude doet de OAuth flow zelf bij de eerste call — popup met inloggen op je
Campai-account, toestemming voor `admcms.read`. Daarna kun je vragen stellen zoals:

> "Wat zijn de top 10 klanten qua omzet dit jaar?"
> "Wat is de marge per productgroep in segment B2B?"
> "Welke producten hebben verdachte prijzen?"

Audit log kun je inzien via:

```powershell
$token = az account get-access-token --resource api://1e6983d3-f996-4581-9518-e6819b79ac6a --query accessToken -o tsv
curl -H "Authorization: Bearer $token" "https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/admin/audit?limit=20" | jq
```

## Optie 3 — Cursor / VS Code MCP extension

Identiek aan Claude Desktop — voeg de MCP server URL toe in de instellingen, OAuth
flow doet de rest.

## Optie 4 — Lokaal draaien (snelste iteratie)

```powershell
git clone https://github.com/Github-campai/AzureMCP-CMS.git
cd AzureMCP-CMS
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

Edit `.env` — voor pure tool-logic debugging zonder Entra:

```
AUTH_DISABLED=true
ADMCMS_SERVER=cmsnl.database.windows.net
ADMCMS_DATABASE=ADMCMS-PRODUCTION-REPL01
ADMCMS_DRIVER=ODBC Driver 18 for SQL Server
ADMCMS_DB_USERNAME=saazuredb
ADMCMS_DB_PASSWORD=<vraag aan Janneke of pak uit je oude .env>
KEY_VAULT_URI=https://cms-mcp-kv-prod.vault.azure.net/
ENTRA_APP_ID=1e6983d3-f996-4581-9518-e6819b79ac6a
ENTRA_ALLOWED_TENANTS=bf030cec-d527-4f3c-9843-db5473bee3f7
```

Start:

```powershell
uvicorn admcms_mcp.server:app --port 8000 --reload
```

Test:
```powershell
curl http://localhost:8000/healthz
curl -X POST -H "Content-Type: application/json" `
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' `
    http://localhost:8000/mcp
```

Met `AUTH_DISABLED=true` accepteert de server alle requests zonder bearer token —
dus alleen lokaal gebruiken, nooit deployen met deze setting.

## Unit tests draaien

```powershell
pytest -v --cov=admcms_mcp
```

60 tests, mock'd DB, geen Azure of echte SQL nodig.

## Logs en debugging

Logs streamen vanaf de live Container App:

```powershell
az login
az account set --subscription 1886dd8f-1ce7-4af2-8491-338341544b55
az containerapp logs show --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod --follow
```

Of via Azure Portal → `CMS-MCP-ai-prod` (Application Insights) → Logs.

Container revisions zien:

```powershell
az containerapp revision list --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod -o table
```

## Code wijzigen → deploy

```powershell
# Maak een feature branch
git checkout -b feat/your-change

# Edit code in src/admcms_mcp/...
# Run tests lokaal
pytest

# Commit + push
git add -A
git commit -m "feat: jouw wijziging"
git push -u origin feat/your-change

# Open PR op GitHub → merge naar main → GitHub Actions deployt automatisch in ~3 min
```

GitHub Actions status: https://github.com/Github-campai/AzureMCP-CMS/actions

## Iets stuk?

| Symptoom | Check |
|---|---|
| `/healthz` 503 | Container start nog op, wacht 30s. Check `az containerapp logs show ...` |
| `/mcp` 401 met `Missing bearer token` | Auth werkt, je vergeet `Authorization` header |
| `/mcp` 401 met `Wrong audience` | Token is voor andere resource, gebruik `--resource api://1e6983d3-...` |
| `/mcp` 403 `Forbidden` | Jouw OID staat niet op de allowlist — vraag Janneke |
| Tool call 500 met DB error | Check SQL firewall (`AllowAllWindowsAzureIps`), wachtwoord in KV |
| Tool call 200 maar lege resultaten | Tool werkt, query geeft 0 rijen — check parameters |

Volledige troubleshooting + ops in [`docs/DEPLOYMENT.md`](DEPLOYMENT.md).
