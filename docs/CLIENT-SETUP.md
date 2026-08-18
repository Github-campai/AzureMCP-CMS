# Client setup — CMS eindgebruikers

Hoe de ADMCMS MCP server toevoegen in je AI-tool van keuze. Voorwaarde:
je CMS IT-admin heeft de tenant al onboarded (zie [`ONBOARDING-CUSTOMER.md`](ONBOARDING-CUSTOMER.md))
en jouw account zit in de security group die toegang heeft.

| | |
|---|---|
| **MCP endpoint** | `https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp` |
| **Scope** | `admcms.read` |
| **Resource (audience)** | `api://1e6983d3-f996-4581-9518-e6819b79ac6a` |

## Alle MCP-clients: alleen de URL

De server publiceert zijn eigen OAuth-metadata en ondersteunt dynamic client
registration, dus geen enkele client heeft nog een client-ID, secret of
proxy-script nodig. Overal geldt: voer de MCP-URL in, log in met je
Microsoft-account, klaar.

| Client | Waar |
|---|---|
| **Claude** (web/desktop) | Settings -> Connectors -> Add custom connector -> URL |
| **Claude Code** | `claude mcp add --transport http admcms <URL>` |
| **Cursor** | Settings -> MCP -> Add new MCP server -> type `http` |
| **VS Code** | Settings -> MCP -> Servers -> `{"admcms": {"type": "http", "url": "<URL>"}}` |

Bij de eerste tool-aanroep opent de Entra-inlogflow. De sessie vernieuwt zichzelf
daarna via de refresh token, dus geen herhaald inloggen elk uur.

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
