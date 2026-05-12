# Client setup — CMS eindgebruikers

Hoe de ADMCMS MCP server toevoegen in je AI-tool van keuze. Voorwaarde:
je CMS IT-admin heeft de tenant al onboarded (zie [`ONBOARDING-CUSTOMER.md`](ONBOARDING-CUSTOMER.md))
en jouw account zit in de security group die toegang heeft.

| | |
|---|---|
| **MCP endpoint** | `https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp` |
| **Scope** | `admcms.read` |
| **Resource (audience)** | `api://1e6983d3-f996-4581-9518-e6819b79ac6a` |

## Claude Desktop

1. Open Claude Desktop
2. Menu → Settings → Developer → "Edit Config"
3. Voeg toe aan `mcpServers`:

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

4. Restart Claude Desktop
5. Bij de eerste vraag waarbij Claude de MCP wil gebruiken, opent een browser
   voor Entra-login. Log in met je CMS-account, geef toestemming voor
   `admcms.read`.
6. Daarna werkt het — vraag bijvoorbeeld: *"Wat zijn onze top 10 klanten dit jaar?"*

## Claude Code (CLI)

```bash
claude mcp add admcms https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp --transport http
```

Eerste call doet de OAuth flow in je browser.

## Cursor

1. Cursor → Settings → MCP
2. **Add new MCP server**
3. Name: `admcms`
4. Type: `http`
5. URL: `https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp`
6. Bij eerste call: OAuth flow in browser

## VS Code (MCP extension)

1. Install de MCP extension (GitHub Copilot MCP support of equivalent)
2. Settings → MCP → Servers → Add:

```json
{
  "admcms": {
    "transport": {
      "type": "http",
      "url": "https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp"
    }
  }
}
```

## Copilot Studio (custom connector)

Voor Copilot Studio agents heb je een **custom connector** met OAuth nodig. Microsoft
heeft sinds Q1 2026 een native MCP connector preview — als die in jouw tenant
beschikbaar is, gebruik die.

Anders, custom connector configuratie:

| Veld | Waarde |
|---|---|
| Authentication type | OAuth 2.0 |
| Identity provider | Azure Active Directory |
| Client ID | `1e6983d3-f996-4581-9518-e6819b79ac6a` |
| Authorization URL | `https://login.microsoftonline.com/<CMS_TENANT_ID>/oauth2/v2.0/authorize` |
| Token URL | `https://login.microsoftonline.com/<CMS_TENANT_ID>/oauth2/v2.0/token` |
| Refresh URL | `https://login.microsoftonline.com/<CMS_TENANT_ID>/oauth2/v2.0/token` |
| Scope | `api://1e6983d3-f996-4581-9518-e6819b79ac6a/admcms.read` |
| Resource URL | `https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp` |

`<CMS_TENANT_ID>` = `a92e7c27-7bb0-4bc3-aea5-89df54c243ad`.

Voeg de Copilot Studio redirect URI toe in Campai's Entra app reg (vraag Janneke):
- `https://global.consent.azure-apim.net/redirect/<connector-id>` (verschijnt na maken)

## Power BI / dashboards

Dashboards kunnen geen interactive OAuth flow doen — niet rechtstreeks op `/mcp`.
Patroon: backend-proxy met On-Behalf-Of flow. Niet in V1, komt later. Voor nu
gebruik je de MCP via een AI-tool die wel OAuth kan (Claude, Copilot Studio).

## Wat als het niet werkt?

| Symptoom | Oplossing |
|---|---|
| AI-tool toont "not authorized" / 401 | Log opnieuw in. Token expired na 1 uur. |
| OAuth popup blijft hangen | Check pop-up blocker. Of probeer eerst `az login` in een terminal — dat doet de consent. |
| "App needs admin consent" | CMS IT-admin moet eenmalig consent geven, zie [`ONBOARDING-CUSTOMER.md`](ONBOARDING-CUSTOMER.md) stap 1. |
| "Forbidden" (403) | Je staat (nog) niet in de security group `CMS-MCP-Users`. Vraag je IT-admin. |
| Trage response | Eerste call na inactieve periode (>5 min) kan ~5 sec duren — Container App schaalt op. |

Contact bij problemen: jouw IT-admin → Janneke Post (Campai, `Janneke@campai.nl`).
