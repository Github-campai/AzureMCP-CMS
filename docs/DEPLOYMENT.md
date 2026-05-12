# Deployment — AzureMCP-CMS

End-to-end procedure to deploy Roland's ADMCMS MCP server to Azure Container Apps
in subscription `CMS-MCP-PAYG` (`1886dd8f-1ce7-4af2-8491-338341544b55`).

## Architecture

All resources live in resource group **`CMS-MCP-rg-prod`** in **westeurope**:

| Resource | Name |
|---|---|
| Container Registry | `cmsmcpacrprod<token>` |
| Key Vault | `cms-mcp-kv-prod` |
| Log Analytics | `CMS-MCP-law-prod` |
| Application Insights | `CMS-MCP-ai-prod` |
| Managed Identity | `CMS-MCP-id-prod` |
| Container Apps Env | `CMS-MCP-env-prod` |
| Container App | `cms-mcp-app-prod` |

## Prerequisites

- Azure CLI logged in: `az login`
- Owner or Contributor on subscription `CMS-MCP-PAYG`
- Permission to create role assignments (Owner or User Access Administrator)
- For Entra app registration: Cloud Application Administrator role in Campai tenant
  (Janneke uses her Global Admin login for this step)

## One-time bootstrap

### 1. Provision infrastructure + first image

```powershell
.\scripts\deploy-container-full.ps1 `
    -SubscriptionId 1886dd8f-1ce7-4af2-8491-338341544b55 `
    -Environment prod
```

This runs Bicep to create all resources, then builds the first image in ACR and
points the Container App at it. Output includes the public FQDN.

The Container App starts with `AUTH_DISABLED=false` and a placeholder `ENTRA_APP_ID`,
so `/mcp` will reject all calls until the Entra app registration is created and
the env var is updated. `/healthz` should respond 200.

### 2. Create Entra app registration

Log in to Azure with a Global Admin account (separate from Janneke's daily account)
and run:

```powershell
az login --tenant bf030cec-d527-4f3c-9843-db5473bee3f7 --use-device-code
.\scripts\create-entra-app.ps1
```

Script outputs `ENTRA_APP_ID=<guid>`. Update the Container App env:

```powershell
az containerapp update `
    --name cms-mcp-app-prod `
    --resource-group CMS-MCP-rg-prod `
    --set-env-vars ENTRA_APP_ID=<guid>
```

### 3. Seed Key Vault secrets

```powershell
$pwd = Read-Host -AsSecureString "ADMCMS DB password"
.\scripts\seed-keyvault.ps1 `
    -SubscriptionId 1886dd8f-1ce7-4af2-8491-338341544b55 `
    -Environment prod `
    -DbPassword $pwd `
    -AllowlistUserOids @("<your-entra-oid>") `
    -AllowlistGroupOids @()
```

### 4. Allow Container App outbound IPs on the SQL server

The ADMCMS Azure SQL server `cmsnl.database.windows.net` blocks connections by
default. Either:

- Enable "Allow Azure services and resources to access this server" on the SQL
  server's firewall (simplest), or
- Whitelist the Container App's outbound IPs:

```powershell
az containerapp show `
    --name cms-mcp-app-prod `
    --resource-group CMS-MCP-rg-prod `
    --query "properties.outboundIpAddresses" -o tsv
```

Then add each IP as a SQL server firewall rule.

### 5. Configure GitHub Actions (federated OIDC)

Create a federated identity credential on the deployer service principal
(or a dedicated CI service principal) so GitHub can authenticate without secrets:

```powershell
$APP_ID = "<your-deployer-app-id>"
az ad app federated-credential create --id $APP_ID --parameters '{
  "name": "github-azuremcp-cms-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:Github-campai/AzureMCP-CMS:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

Then add three GitHub repository secrets:

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | App ID of the deployer SP |
| `AZURE_TENANT_ID` | `bf030cec-d527-4f3c-9843-db5473bee3f7` |
| (subscription is hardcoded in workflow env) | — |

## Continuous deployment

After bootstrap, every push to `main` that touches `src/`, `Dockerfile`, or
`pyproject.toml` triggers the `Deploy CMS-MCP to Azure Container Apps` workflow.
End-to-end ~3 minutes.

To deploy manually:

```powershell
.\scripts\deploy-container.ps1 -SubscriptionId 1886dd8f-1ce7-4af2-8491-338341544b55
```

## Operations

### View logs

```powershell
az containerapp logs show `
    --name cms-mcp-app-prod `
    --resource-group CMS-MCP-rg-prod `
    --follow
```

### Restart

```powershell
az containerapp revision restart `
    --name cms-mcp-app-prod `
    --resource-group CMS-MCP-rg-prod `
    --revision $(az containerapp revision list --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod --query "[0].name" -o tsv)
```

### Rollback

List revisions, then activate a previous one:

```powershell
az containerapp revision list --name cms-mcp-app-prod --resource-group CMS-MCP-rg-prod -o table
az containerapp revision activate --revision <previous-revision-name> --resource-group CMS-MCP-rg-prod
```

### Health check

```powershell
curl https://<container-app-fqdn>/healthz
curl https://<container-app-fqdn>/.well-known/oauth-protected-resource
```
