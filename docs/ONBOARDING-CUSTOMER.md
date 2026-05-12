# Klant-onboarding — CMS Lelystad

Hoe geef je een klant-tenant toegang tot de CMS-MCP server, zodat hun gebruikers
de MCP kunnen aanroepen vanuit Claude, Copilot Studio en (later) dashboards.

## Architectuur (auth-flow)

```
CMS-gebruiker → Claude/Copilot → krijgt Entra token van CMS tenant
                                  (audience = CMS-MCP-API client ID,
                                   scope = admcms.read)
              → token naar https://cms-mcp-app-prod.<env>.azurecontainerapps.io/mcp
              → server (in Campai tenant) valideert:
                 - signature via JWKS (login.microsoftonline.com)
                 - aud == ENTRA_APP_ID
                 - tid in ENTRA_ALLOWED_TENANTS
                 - scope bevat admcms.read
                 - oid/groups in Key Vault allowlist
              → tool call → Azure SQL ADMCMS → resultaat
```

De **app registratie staat in Campai tenant** (`bf030cec-...`), maar is
**multi-tenant** zodat klanten in hun eigen tenant admin-consent kunnen geven.

## Stap 1 — Admin-consent (CMS IT-admin)

CMS IT-admin opent de admin-consent URL (vervang `<CMS_TENANT_ID>` en
`<CMS_MCP_APP_ID>` — die laatste komt uit `scripts/create-entra-app.ps1`):

```
https://login.microsoftonline.com/<CMS_TENANT_ID>/adminconsent?client_id=<CMS_MCP_APP_ID>
```

Resultaat: de CMS-MCP-API verschijnt als enterprise application in CMS tenant.

## Stap 2 — Security group in CMS tenant

CMS IT-admin maakt een security group `CMS-MCP-Users` in hun tenant en voegt
gebruikers toe die toegang nodig hebben. Hij stuurt het Group Object ID door.

## Stap 3 — Tenant + group toevoegen aan server (Campai)

Voeg `<CMS_TENANT_ID>` toe aan `ENTRA_ALLOWED_TENANTS`:

```powershell
$current = az containerapp show --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod `
    --query "properties.template.containers[0].env[?name=='ENTRA_ALLOWED_TENANTS'].value | [0]" -o tsv
$new = "$current,<CMS_TENANT_ID>"
az containerapp update --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod `
    --set-env-vars "ENTRA_ALLOWED_TENANTS=$new"
```

Voeg het Group OID toe aan de Key Vault allowlist:

```powershell
$secret = az keyvault secret show --vault-name cms-mcp-kv-prod --name admcms-allowlist --query value -o tsv
$obj = $secret | ConvertFrom-Json
$obj.groups += "<CMS_GROUP_OID>"
$updated = $obj | ConvertTo-Json -Compress
az keyvault secret set --vault-name cms-mcp-kv-prod --name admcms-allowlist --value $updated
```

## Stap 4 — Client setup per applicatie

### Claude Desktop / Claude Code (CMS-gebruiker)

Voeg toe aan `~/.claude/mcp_settings.json` (Claude Desktop) of via `claude mcp add`:

```json
{
  "mcpServers": {
    "admcms": {
      "url": "https://<container-app-fqdn>/mcp",
      "transport": "http"
    }
  }
}
```

Claude vraagt bij eerste call om Entra login (interactive). Gebruiker logt in met
CMS-account, geeft toestemming voor `admcms.read`, klaar.

### Cursor / VS Code

Identiek aan Claude — voeg de MCP-server URL toe via de MCP-instellingen.

### Copilot Studio

Configureer een custom connector met:

- **Authentication**: OAuth 2.0 (Generic OAuth 2)
- **Client ID**: `<CMS_MCP_APP_ID>`
- **Authorization URL**: `https://login.microsoftonline.com/<CMS_TENANT_ID>/oauth2/v2.0/authorize`
- **Token URL**: `https://login.microsoftonline.com/<CMS_TENANT_ID>/oauth2/v2.0/token`
- **Scope**: `api://cms-mcp/admcms.read`
- **MCP endpoint**: `https://<container-app-fqdn>/mcp`

### Dashboards (Power BI etc.)

Niet rechtstreeks — dashboards kunnen geen interactive OAuth flow doen. Patroon:
backend-proxy met On-Behalf-Of flow (geplanned, niet in V1; zie inforcer
`docs/SYSTEM_ACCESS_AND_INTEGRATIONS.md` voor blueprint).

## Verifiëren

CMS-gebruiker test in Claude met:

> "Wat zijn de top 10 klanten dit jaar?"

Claude roept tool `get_top_customers` aan, krijgt resultaat, audit log registreert
`upn`, `oid`, `tid`, duur, query.

## Toegang intrekken

- **Per gebruiker**: verwijder uit security group `CMS-MCP-Users` (in CMS tenant).
- **Per tenant**: verwijder `<CMS_TENANT_ID>` uit `ENTRA_ALLOWED_TENANTS`.
- **Volledig (nuke)**: verwijder de service principal van CMS-MCP-API uit CMS tenant
  (CMS IT-admin in Azure Portal → Enterprise applications → CMS-MCP-API → Delete).
