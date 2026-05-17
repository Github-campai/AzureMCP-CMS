# Deployment & ops — voor Campai

Bootstrap is **al gedaan** (mei 2026). Dit document beschrijft hoe je doorlopend
deploys doet, troubleshoot, en eventueel een dev-env opzet.

## Live state

| Resource | Naam |
|---|---|
| Subscription | `CMS-MCP-PAYG` (`1886dd8f-1ce7-4af2-8491-338341544b55`) |
| Resource Group | `CMS-MCP-rg-prod` (westeurope) |
| Container App | `cms-mcp-app-prod` |
| Container Registry | `cmsmcpacrprodcr3e.azurecr.io` |
| Key Vault | `cms-mcp-kv-prod` |
| Managed Identity | `CMS-MCP-id-prod` (client ID `c8d4ca55-662a-4341-abed-3e611026bcb7`) |
| Application Insights | `CMS-MCP-ai-prod` |
| Log Analytics | `CMS-MCP-law-prod` |
| Container Apps Env | `CMS-MCP-env-prod` |
| FQDN | `cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io` |
| Entra App ID | `1e6983d3-f996-4581-9518-e6819b79ac6a` |
| CI service principal | `b5ea2a86-3877-492e-95c0-db2e5263448f` (GitHub federated OIDC) |
| Bootstrap deployment naam | `cms-mcp-prod-...` (zie `az deployment sub list`) |

## Toegangsrechten op de RG

| Principal | Rol |
|---|---|
| Janneke@campai.nl | Contributor (RG) + Key Vault Secrets Officer (KV) |
| Break-glass / GA accounts | Subscription Owner (inherited) |
| CMS-MCP-GitHubActions SP | Contributor (RG) + AcrPush (ACR), federated OIDC, alleen `main` branch |
| Managed Identity `CMS-MCP-id-prod` | Key Vault Secrets User + AcrPull |

## Doorlopende deploys

### Optie 1 — Automatisch via GitHub Actions (default)

Push naar `main` → workflow `.github/workflows/deploy-to-aca.yml` triggert
automatisch op wijzigingen in `src/`, `Dockerfile`, of `pyproject.toml`. ~3 min
end-to-end. Watch: https://github.com/Github-campai/AzureMCP-CMS/actions

### Optie 2 — Handmatig vanaf je laptop

```powershell
git pull
cd AzureMCP-CMS
.\scripts\deploy-container.ps1 -SubscriptionId 1886dd8f-1ce7-4af2-8491-338341544b55
```

### Optie 3 — Snelle inline build

```powershell
az account set --subscription 1886dd8f-1ce7-4af2-8491-338341544b55
$tag = (Get-Date -Format 'yyyyMMddHHmmss')
az acr build --registry cmsmcpacrprodcr3e --image "admcms-mcp:$tag" --image "admcms-mcp:latest" --file Dockerfile .
az containerapp update --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod --image "cmsmcpacrprodcr3e.azurecr.io/admcms-mcp:$tag"
```

## Toegangsmodel

Authorisatie via Key Vault secret `admcms-allowlist`. Structure:

```json
{
  "users":  ["<oid>", ...],   // individuele Entra OIDs (fallback)
  "groups": ["<oid>", ...]    // Entra security group OIDs (voorkeur)
}
```

**Huidige groups:**

| Group | OID | Tenant | Beheerd door |
|---|---|---|---|
| `SG-Campai-ADMCMS-Users` | `bd6ce8cc-033a-482e-a7cd-acd145e6d93f` | Campai (`bf030cec-...`) | Roland + Janneke (owners) |
| `SG-CMS-MCP-Users` | `68f3ad86-6967-4540-92e5-b8b425ca720d` | CMS Lelystad (`a92e7c27-...`) | CMS IT-admin |

**Nieuwe Campai-user toevoegen**: Entra portal → Groups → `SG-Campai-ADMCMS-Users` → Members → + Add. Direct toegang, geen KV-touch, geen restart.

**Nieuwe CMS-user toevoegen**: CMS IT-admin doet hetzelfde in hun eigen tenant op `SG-CMS-MCP-Users`. Geen actie van Campai-kant.

## Key Vault secrets beheren

Janneke@campai.nl heeft `Key Vault Secrets Officer` op `cms-mcp-kv-prod`. Direct
vanaf daily account:

```powershell
az account set --subscription 1886dd8f-1ce7-4af2-8491-338341544b55

# DB password roteren
az keyvault secret set --vault-name cms-mcp-kv-prod --name admcms-db-password --value '<nieuw>'

# Allowlist updaten — extra group OID toevoegen (zelden nodig — alleen voor nieuwe klant)
$current = az keyvault secret show --vault-name cms-mcp-kv-prod --name admcms-allowlist --query value -o tsv | ConvertFrom-Json
$current.groups = @($current.groups) + "<NEW_GROUP_OID>"
$updated = $current | ConvertTo-Json -Compress
az keyvault secret set --vault-name cms-mcp-kv-prod --name admcms-allowlist --value $updated

# Container App restarten zodat hij de nieuwe waardes oppikt (cache 5 min)
$rev = az containerapp revision list -n cms-mcp-app-prod -g CMS-MCP-rg-prod --query "[?properties.active].name | [0]" -o tsv
az containerapp revision restart -n cms-mcp-app-prod -g CMS-MCP-rg-prod --revision $rev
```

## Diagnostics — health_check MCP tool

Naast `/healthz` (HTTP) is er een MCP-tool `health_check` aanroepbaar vanuit
Claude/Copilot/Cursor:

> *"Roep de admcms health_check tool aan."*

Returnt server-status + database latency + caller-identity. Handig voor
gebruikers om te verifiëren dat alles werkt voor ze een echte vraag stellen.

## Tenant toevoegen aan `ENTRA_ALLOWED_TENANTS`

Wanneer een tweede klant onboarded:

```powershell
$current = az containerapp show -n cms-mcp-app-prod -g CMS-MCP-rg-prod --query "properties.template.containers[0].env[?name=='ENTRA_ALLOWED_TENANTS'].value | [0]" -o tsv
$new = "$current,<NEW_TENANT_ID>"
az containerapp update -n cms-mcp-app-prod -g CMS-MCP-rg-prod --set-env-vars "ENTRA_ALLOWED_TENANTS=$new"
```

## Logs & monitoring

### Live logs streamen

```powershell
az containerapp logs show --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod --follow
```

### Recente logs (geen follow)

```powershell
az containerapp logs show --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod --tail 200
```

### Systeem-events (image pulls, probe failures)

```powershell
az containerapp logs show --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod --type system --tail 50
```

### Application Insights

Azure Portal → `CMS-MCP-ai-prod` → Logs (KQL).

Voorbeelden:

```kusto
// Tool calls in laatste uur
requests | where timestamp > ago(1h) | where url contains "/mcp" | summarize count() by resultCode

// Errors
exceptions | where timestamp > ago(24h) | order by timestamp desc

// Avg response time per endpoint
requests | summarize avg(duration), count() by name | order by count_ desc
```

## Revisions & rollback

Lijst van revisions:

```powershell
az containerapp revision list --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod -o table
```

Rollback naar een eerdere revisie:

```powershell
# Deactiveer huidige
az containerapp revision deactivate --revision <current> --resource-group CMS-MCP-rg-prod

# Activeer eerdere
az containerapp revision activate --revision <previous> --resource-group CMS-MCP-rg-prod
```

Of: update image-tag naar eerdere SHA:

```powershell
az containerapp update --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod --image cmsmcpacrprodcr3e.azurecr.io/admcms-mcp:<previous-sha>
```

## Infra wijzigen

Bicep templates in `infra/`. Wijzigingen → opnieuw deployen:

```powershell
$myOid = az ad signed-in-user show --query id -o tsv
az deployment sub create `
    --name "cms-mcp-prod-$(Get-Date -Format 'yyyyMMddHHmmss')" `
    --location westeurope `
    --template-file infra/main.bicep `
    --parameters `
        environmentName=prod `
        location=westeurope `
        deployerPrincipalId=$myOid `
        entraAppId=1e6983d3-f996-4581-9518-e6819b79ac6a `
        entraAllowedTenants="bf030cec-d527-4f3c-9843-db5473bee3f7,a92e7c27-7bb0-4bc3-aea5-89df54c243ad"
```

⚠️ Daily account kan dit niet alleen — Bicep maakt role assignments aan en
daarvoor heb je Owner of User Access Administrator nodig. Voor pure
resource-wijzigingen (geen RBAC), Contributor is genoeg.

## Dev environment opzetten (optioneel)

Voor een tweede environment (`-dev` met scale-to-zero):

```powershell
.\scripts\deploy-container-full.ps1 -SubscriptionId 1886dd8f-... -Environment dev
```

Vereist Owner-rechten (break-glass account). Maakt een tweede RG `CMS-MCP-rg-dev`
met alle resources `CMS-MCP-{kind}-dev`.

## Troubleshooting tabel

| Symptoom | Eerste check | Actie |
|---|---|---|
| `/healthz` 503 | Logs (`az containerapp logs show ...`) | Wacht 30s, herstart als persistent |
| Alle calls 401 | Token expired bij client | Client moet opnieuw inloggen |
| Alle calls 403 | Allowlist | Check KV `admcms-allowlist`, restart na update |
| Tool calls 500 SQL error | DB password verlopen? Firewall? | Roteer KV `admcms-db-password`, check SQL firewall |
| Image pull error | ACR auth | Check Managed Identity heeft AcrPull op `cmsmcpacrprodcr3e` |
| GitHub Actions deploy faalt | OIDC | Check federated credential op CI SP `b5ea2a86-...` voor branch |
| Audit log groeit te hard | `data/audit.db` in container | Container Apps storage is ephemeral — audit is per-replica. Later: migrate naar Azure Table (Roland TODO). |

## Verwante docs

- [Testing (voor Roland)](TESTING.md)
- [Klant-onboarding (voor CMS IT-admin)](ONBOARDING-CUSTOMER.md)
- [Client setup (voor eindgebruikers)](CLIENT-SETUP.md)
- [Operating model (wie doet wat)](OPERATING-MODEL.md)
- [Design spec](superpowers/specs/2026-05-12-azuremcp-cms-design.md)
