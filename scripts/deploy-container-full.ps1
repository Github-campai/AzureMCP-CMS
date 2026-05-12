# Bootstrap deployment for AzureMCP-CMS.
# One-shot: provisions all Azure resources via Bicep, builds first image in ACR,
# updates Container App to use it.
#
# Run from repo root:
#   .\scripts\deploy-container-full.ps1 -SubscriptionId 1886dd8f-1ce7-4af2-8491-338341544b55
#
# Prereqs: az CLI logged in (az login), permission to create role assignments.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter()]
    [ValidateSet('dev', 'prod', 'staging')]
    [string]$Environment = 'prod',

    [Parameter()]
    [string]$Location = 'westeurope',

    [Parameter()]
    [string]$EntraAppId = '00000000-0000-0000-0000-000000000000',

    [Parameter()]
    [string]$EntraAllowedTenants = 'bf030cec-d527-4f3c-9843-db5473bee3f7',

    [Parameter()]
    [switch]$SkipImageBuild
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "=== AzureMCP-CMS bootstrap deploy ===" -ForegroundColor Cyan
Write-Host "  Subscription: $SubscriptionId"
Write-Host "  Environment:  $Environment"
Write-Host "  Location:     $Location"
Write-Host ""

# 1. Set subscription context
az account set --subscription $SubscriptionId
$account = az account show -o json | ConvertFrom-Json
Write-Host "Active subscription: $($account.name)" -ForegroundColor Green

# 2. Resolve deployer principal ID
$deployerPrincipalId = az ad signed-in-user show --query id -o tsv
if (-not $deployerPrincipalId) {
    throw "Could not resolve signed-in user principal ID. Run 'az login' first."
}
Write-Host "Deployer principal: $deployerPrincipalId"

# 3. Deploy Bicep (subscription scope — creates RG + all resources)
$deploymentName = "cms-mcp-$Environment-$(Get-Date -Format 'yyyyMMddHHmmss')"
Write-Host ""
Write-Host "Deploying Bicep template (deployment name: $deploymentName)..." -ForegroundColor Cyan

$deployment = az deployment sub create `
    --name $deploymentName `
    --location $Location `
    --template-file "$repoRoot/infra/main.bicep" `
    --parameters `
        environmentName=$Environment `
        location=$Location `
        deployerPrincipalId=$deployerPrincipalId `
        entraAppId=$EntraAppId `
        entraAllowedTenants=$EntraAllowedTenants `
    --output json | ConvertFrom-Json

$outputs = $deployment.properties.outputs
$rgName = $outputs.AZURE_RESOURCE_GROUP.value
$acrName = $outputs.CONTAINER_REGISTRY_NAME.value
$acrLoginServer = $outputs.CONTAINER_REGISTRY_LOGIN_SERVER.value
$containerAppName = $outputs.CONTAINER_APP_NAME.value
$keyVaultName = $outputs.KEY_VAULT_NAME.value
$appFqdn = $outputs.CONTAINER_APP_FQDN.value

Write-Host ""
Write-Host "Infrastructure provisioned:" -ForegroundColor Green
Write-Host "  Resource Group:     $rgName"
Write-Host "  Container Registry: $acrLoginServer"
Write-Host "  Container App:      $containerAppName"
Write-Host "  Key Vault:          $keyVaultName"
Write-Host "  App URL:            https://$appFqdn"

# 4. Build image in ACR (cloud-side, no local Docker)
if (-not $SkipImageBuild) {
    $imageTag = (Get-Date -Format 'yyyyMMddHHmmss')
    $imageName = "admcms-mcp"
    Write-Host ""
    Write-Host "Building image $imageName`:$imageTag in ACR $acrName..." -ForegroundColor Cyan
    az acr build `
        --registry $acrName `
        --image "${imageName}:${imageTag}" `
        --image "${imageName}:latest" `
        --file "$repoRoot/Dockerfile" `
        "$repoRoot"

    Write-Host "Updating Container App to use new image..." -ForegroundColor Cyan
    az containerapp update `
        --name $containerAppName `
        --resource-group $rgName `
        --image "${acrLoginServer}/${imageName}:${imageTag}" `
        --output none
}

Write-Host ""
Write-Host "=== Bootstrap complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Create Entra app registration (scripts/create-entra-app.ps1)"
Write-Host "  2. Seed Key Vault secrets (scripts/seed-keyvault.ps1)"
Write-Host "  3. Update Container App env: ENTRA_APP_ID, ENTRA_ALLOWED_TENANTS"
Write-Host "  4. Test:  curl https://$appFqdn/healthz"
