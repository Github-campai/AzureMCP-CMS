# Incremental deploy: build new image, update Container App.
# Use this for code-only changes (no infrastructure changes).
#
# Run from repo root:
#   .\scripts\deploy-container.ps1 -SubscriptionId 1886dd8f-...

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter()]
    [ValidateSet('dev', 'prod', 'staging')]
    [string]$Environment = 'prod',

    [Parameter()]
    [string]$ResourceGroup,

    [Parameter()]
    [string]$ContainerRegistry,

    [Parameter()]
    [string]$ContainerApp
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

az account set --subscription $SubscriptionId

if (-not $ResourceGroup) { $ResourceGroup = "CMS-MCP-rg-$Environment" }
if (-not $ContainerApp) { $ContainerApp = "cms-mcp-app-$Environment" }
if (-not $ContainerRegistry) {
    $ContainerRegistry = az acr list --resource-group $ResourceGroup --query "[0].name" -o tsv
    if (-not $ContainerRegistry) { throw "No ACR found in $ResourceGroup." }
}

$imageTag = (Get-Date -Format 'yyyyMMddHHmmss')
$imageName = 'admcms-mcp'

Write-Host "Building $imageName`:$imageTag in $ContainerRegistry..." -ForegroundColor Cyan
az acr build `
    --registry $ContainerRegistry `
    --image "${imageName}:${imageTag}" `
    --image "${imageName}:latest" `
    --file "$repoRoot/Dockerfile" `
    "$repoRoot"

$loginServer = az acr show --name $ContainerRegistry --query loginServer -o tsv
Write-Host "Updating Container App $ContainerApp..." -ForegroundColor Cyan
az containerapp update `
    --name $ContainerApp `
    --resource-group $ResourceGroup `
    --image "${loginServer}/${imageName}:${imageTag}" `
    --output none

$fqdn = az containerapp show --name $ContainerApp --resource-group $ResourceGroup --query "properties.configuration.ingress.fqdn" -o tsv
Write-Host ""
Write-Host "Deployed: https://$fqdn" -ForegroundColor Green
