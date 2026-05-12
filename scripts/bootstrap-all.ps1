# ============================================================================
# AzureMCP-CMS — END-TO-END BOOTSTRAP
# ============================================================================
#
# RUN THIS WITH YOUR ELEVATED / BREAK-GLASS / GA ACCOUNT
# (e.g. cmp-breakglass-tq1072@Campai.onmicrosoft.com)
#
# This account needs:
#   - Owner on subscription 1886dd8f-1ce7-4af2-8491-338341544b55, AND
#   - Application Administrator or Cloud Application Administrator in
#     Campai tenant (for the Entra app registration step).
#
# What this script does (idempotent — safe to re-run):
#   1. Deploys all Azure infra via Bicep (RG, ACR, KeyVault, Container App,
#      Managed Identity, App Insights, Log Analytics).
#   2. Creates the multi-tenant Entra app registration 'CMS-MCP-API' with
#      the admcms.read scope.
#   3. Builds Roland's MCP server image from this repo into the ACR.
#   4. Updates the Container App to use that image.
#   5. Wires up federated OIDC for GitHub Actions (so daily deploys don't
#      need your GA account anymore).
#   6. Grants your daily account (Janneke@campai.nl) Contributor + Key Vault
#      Secrets Officer in the resource group, so you can run ops scripts.
#
# USAGE:
#   1. Open PowerShell, then:
#        az login --use-device-code
#        # log in with your GA account in the device-code URL
#   2. Cd to the repo root:
#        cd C:\path\to\AzureMCP-CMS
#   3. Run:
#        .\scripts\bootstrap-all.ps1
#
# ============================================================================

[CmdletBinding()]
param(
    [string]$SubscriptionId = '1886dd8f-1ce7-4af2-8491-338341544b55',
    [string]$Environment = 'prod',
    [string]$Location = 'westeurope',
    [string]$CampaiTenantId = 'bf030cec-d527-4f3c-9843-db5473bee3f7',

    # Daily account that should retain ops access after bootstrap.
    [string]$DailyOpsUserUpn = 'Janneke@campai.nl',

    # GitHub repo for federated identity credential.
    [string]$GitHubRepo = 'Github-campai/AzureMCP-CMS',
    [string]$GitHubBranch = 'main'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "    --  $msg" -ForegroundColor Gray }

# ---------------------------------------------------------------------------
# 0. Verify context
# ---------------------------------------------------------------------------
Write-Step "Verifying Azure CLI context"
az account set --subscription $SubscriptionId
$ctx = az account show -o json | ConvertFrom-Json
Write-Info "Subscription: $($ctx.name) ($($ctx.id))"
Write-Info "Logged in as: $($ctx.user.name)"

$myOid = az ad signed-in-user show --query id -o tsv
$myUpn = az ad signed-in-user show --query userPrincipalName -o tsv
Write-Info "Principal OID: $myOid"

# Verify Owner role
$roles = az role assignment list --assignee $myOid --include-inherited `
    --scope "/subscriptions/$SubscriptionId" `
    --query "[].roleDefinitionName" -o tsv
if ($roles -notcontains 'Owner') {
    Write-Warning "You are not Owner on this subscription. Current roles: $($roles -join ', ')"
    Write-Warning "Role assignments inside Bicep will fail. Continue at your own risk."
    $confirm = Read-Host "Continue anyway? (y/N)"
    if ($confirm -ne 'y') { exit 1 }
}
Write-Ok "Context verified."

# ---------------------------------------------------------------------------
# 1. Create Entra app registration FIRST (so its appId is in Bicep)
# ---------------------------------------------------------------------------
Write-Step "Creating Entra app registration 'CMS-MCP-API'"

$appDisplayName = 'CMS-MCP-API'
$identifierUri = 'api://cms-mcp'

$existing = az ad app list --display-name $appDisplayName --query "[0]" -o json | ConvertFrom-Json
if ($existing -and $existing.appId) {
    Write-Info "App registration already exists, reusing: $($existing.appId)"
    $appId = $existing.appId
    $appObjectId = $existing.id
} else {
    $app = az ad app create `
        --display-name $appDisplayName `
        --sign-in-audience AzureADMultipleOrgs `
        -o json | ConvertFrom-Json
    $appId = $app.appId
    $appObjectId = $app.id
    Write-Ok "App created: appId=$appId"

    # Set identifier URI (must be done after create because it requires appId in URI)
    az ad app update --id $appObjectId --identifier-uris $identifierUri | Out-Null
    Write-Info "Identifier URI set: $identifierUri"

    # Add the admcms.read scope via Microsoft Graph
    $scopeId = [guid]::NewGuid().ToString()
    $scopePayload = @{
        api = @{
            oauth2PermissionScopes = @(
                @{
                    id                      = $scopeId
                    adminConsentDescription = 'Allows the app to read ADMCMS data on behalf of the signed-in user'
                    adminConsentDisplayName = 'Read ADMCMS data'
                    userConsentDescription  = 'Allow the app to read ADMCMS data on your behalf'
                    userConsentDisplayName  = 'Read ADMCMS data'
                    value                   = 'admcms.read'
                    type                    = 'User'
                    isEnabled               = $true
                }
            )
        }
    } | ConvertTo-Json -Depth 10 -Compress

    $tmp = New-TemporaryFile
    $scopePayload | Out-File -FilePath $tmp -Encoding utf8
    try {
        az rest --method PATCH `
            --uri "https://graph.microsoft.com/v1.0/applications/$appObjectId" `
            --headers "Content-Type=application/json" `
            --body "@$tmp" | Out-Null
    } finally { Remove-Item $tmp -Force }
    Write-Ok "Scope 'admcms.read' added"

    # Create the home-tenant service principal
    az ad sp create --id $appId 2>$null | Out-Null
    Write-Info "Home-tenant service principal created"
}

# ---------------------------------------------------------------------------
# 2. Deploy Bicep (infra)
# ---------------------------------------------------------------------------
Write-Step "Deploying Bicep (this creates the resource group + all resources)"

$deploymentName = "cms-mcp-$Environment-$(Get-Date -Format 'yyyyMMddHHmmss')"
$deployment = az deployment sub create `
    --name $deploymentName `
    --location $Location `
    --template-file "$repoRoot/infra/main.bicep" `
    --parameters `
        environmentName=$Environment `
        location=$Location `
        deployerPrincipalId=$myOid `
        entraAppId=$appId `
        entraAllowedTenants=$CampaiTenantId `
    -o json | ConvertFrom-Json

$out = $deployment.properties.outputs
$rgName            = $out.AZURE_RESOURCE_GROUP.value
$acrName           = $out.CONTAINER_REGISTRY_NAME.value
$acrLoginServer    = $out.CONTAINER_REGISTRY_LOGIN_SERVER.value
$containerAppName  = $out.CONTAINER_APP_NAME.value
$containerAppFqdn  = $out.CONTAINER_APP_FQDN.value
$keyVaultName      = $out.KEY_VAULT_NAME.value
$keyVaultUri       = $out.KEY_VAULT_URI.value
$managedIdentityClientId = $out.MANAGED_IDENTITY_CLIENT_ID.value

Write-Ok "Resource group:    $rgName"
Write-Ok "Container Registry:$acrLoginServer"
Write-Ok "Key Vault:         $keyVaultName"
Write-Ok "Container App:     https://$containerAppFqdn"
Write-Ok "Managed Identity client ID: $managedIdentityClientId"

# ---------------------------------------------------------------------------
# 3. Build Roland's image into ACR + update Container App
# ---------------------------------------------------------------------------
Write-Step "Building admcms-mcp image in ACR (cloud-side, ~3-5 min)"

$imageTag = (Get-Date -Format 'yyyyMMddHHmmss')
az acr build `
    --registry $acrName `
    --image "admcms-mcp:$imageTag" `
    --image "admcms-mcp:latest" `
    --file "$repoRoot/Dockerfile" `
    "$repoRoot"

Write-Step "Updating Container App to use new image"
az containerapp update `
    --name $containerAppName `
    --resource-group $rgName `
    --image "$acrLoginServer/admcms-mcp:$imageTag" `
    -o none
Write-Ok "Container App updated to admcms-mcp:$imageTag"

# ---------------------------------------------------------------------------
# 4. Grant daily account ops access on the resource group
# ---------------------------------------------------------------------------
Write-Step "Granting $DailyOpsUserUpn ops access on $rgName"

$dailyOid = az ad user show --id $DailyOpsUserUpn --query id -o tsv 2>$null
if ($dailyOid) {
    az role assignment create `
        --assignee $dailyOid `
        --role 'Contributor' `
        --scope "/subscriptions/$SubscriptionId/resourceGroups/$rgName" `
        -o none 2>$null
    az role assignment create `
        --assignee $dailyOid `
        --role 'Key Vault Secrets Officer' `
        --scope "/subscriptions/$SubscriptionId/resourceGroups/$rgName/providers/Microsoft.KeyVault/vaults/$keyVaultName" `
        -o none 2>$null
    Write-Ok "$DailyOpsUserUpn now Contributor + KV Secrets Officer on $rgName"
} else {
    Write-Warning "Could not resolve $DailyOpsUserUpn — skip role assignment."
}

# ---------------------------------------------------------------------------
# 5. Create service principal + federated identity for GitHub Actions
# ---------------------------------------------------------------------------
Write-Step "Setting up federated OIDC for GitHub Actions"

$ciAppName = 'CMS-MCP-GitHubActions'
$ciApp = az ad app list --display-name $ciAppName --query "[0]" -o json | ConvertFrom-Json
if (-not $ciApp -or -not $ciApp.appId) {
    $ciApp = az ad app create --display-name $ciAppName --sign-in-audience AzureADMyOrg -o json | ConvertFrom-Json
    az ad sp create --id $ciApp.appId 2>$null | Out-Null
    Write-Ok "CI service principal created: appId=$($ciApp.appId)"
} else {
    Write-Info "CI app already exists: $($ciApp.appId)"
}
$ciAppId = $ciApp.appId
$ciAppObjectId = $ciApp.id
$ciSpObjectId = az ad sp show --id $ciAppId --query id -o tsv

# Federated credential for main branch
$fedName = "github-azuremcp-cms-$GitHubBranch"
$fedExists = az ad app federated-credential list --id $ciAppObjectId --query "[?name=='$fedName']" -o json | ConvertFrom-Json
if (-not $fedExists -or $fedExists.Count -eq 0) {
    $fedBody = @{
        name      = $fedName
        issuer    = 'https://token.actions.githubusercontent.com'
        subject   = "repo:${GitHubRepo}:ref:refs/heads/$GitHubBranch"
        audiences = @('api://AzureADTokenExchange')
    } | ConvertTo-Json -Compress

    $tmp = New-TemporaryFile
    $fedBody | Out-File -FilePath $tmp -Encoding utf8
    try {
        az ad app federated-credential create --id $ciAppObjectId --parameters "@$tmp" | Out-Null
    } finally { Remove-Item $tmp -Force }
    Write-Ok "Federated credential created for $GitHubRepo branch $GitHubBranch"
} else {
    Write-Info "Federated credential already exists"
}

# Grant the CI SP rights on the RG
az role assignment create `
    --assignee $ciSpObjectId `
    --role 'Contributor' `
    --scope "/subscriptions/$SubscriptionId/resourceGroups/$rgName" `
    -o none 2>$null
az role assignment create `
    --assignee $ciSpObjectId `
    --role 'AcrPush' `
    --scope "/subscriptions/$SubscriptionId/resourceGroups/$rgName/providers/Microsoft.ContainerRegistry/registries/$acrName" `
    -o none 2>$null
Write-Ok "CI service principal granted Contributor + AcrPush on $rgName"

# ---------------------------------------------------------------------------
# Final report
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  BOOTSTRAP COMPLETE" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Container App URL:   https://$containerAppFqdn"
Write-Host "Health check:        https://$containerAppFqdn/healthz"
Write-Host "OAuth metadata:      https://$containerAppFqdn/.well-known/oauth-protected-resource"
Write-Host ""
Write-Host "Entra app:"
Write-Host "  Display name:      $appDisplayName"
Write-Host "  Client ID:         $appId"
Write-Host "  Identifier URI:    $identifierUri"
Write-Host "  Scope:             admcms.read"
Write-Host ""
Write-Host "GitHub Actions secrets to add at" -ForegroundColor Yellow
Write-Host "  https://github.com/$GitHubRepo/settings/secrets/actions" -ForegroundColor Yellow
Write-Host ""
Write-Host "  AZURE_CLIENT_ID    = $ciAppId"
Write-Host "  AZURE_TENANT_ID    = $CampaiTenantId"
Write-Host ""
Write-Host "TODO (after this script):"
Write-Host "  1. Seed Key Vault secrets (DB password + allowlist):"
Write-Host "       .\scripts\seed-keyvault.ps1 -SubscriptionId $SubscriptionId ``"
Write-Host "           -Environment $Environment ``"
Write-Host "           -DbPassword (Read-Host -AsSecureString 'DB password') ``"
Write-Host "           -AllowlistUserOids @('$myOid')"
Write-Host "  2. Open the SQL server firewall:"
Write-Host "       az sql server firewall-rule create -g <admcms-rg> -s cmsnl ``"
Write-Host "           -n AllowAzure --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0"
Write-Host "     (or whitelist the Container App outbound IPs)"
Write-Host "  3. When CMS Lelystad tenant ID is known:"
Write-Host "       az containerapp update --name $containerAppName -g $rgName ``"
Write-Host "         --set-env-vars 'ENTRA_ALLOWED_TENANTS=$CampaiTenantId,<CMS_TENANT_ID>'"
Write-Host "  4. Send admin-consent URL to CMS IT-admin:"
Write-Host "       https://login.microsoftonline.com/<CMS_TENANT_ID>/adminconsent?client_id=$appId"
Write-Host ""
