# Seed Key Vault with required secrets for the ADMCMS MCP server.
# Requires Key Vault Secrets Officer role (deploy-container-full.ps1 grants this).
#
# Secrets created:
#   - admcms-db-password   (SQL auth password for ADMCMS)
#   - admcms-allowlist     (JSON: {"users": [...oid...], "groups": [...oid...]})

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter()]
    [ValidateSet('dev', 'prod', 'staging')]
    [string]$Environment = 'prod',

    [Parameter()]
    [string]$KeyVaultName,

    [Parameter(Mandatory = $true)]
    [SecureString]$DbPassword,

    [Parameter()]
    [string[]]$AllowlistUserOids = @(),

    [Parameter()]
    [string[]]$AllowlistGroupOids = @()
)

$ErrorActionPreference = 'Stop'

az account set --subscription $SubscriptionId

if (-not $KeyVaultName) { $KeyVaultName = "cms-mcp-kv-$Environment" }

Write-Host "Seeding Key Vault $KeyVaultName..." -ForegroundColor Cyan

$plain = [System.Net.NetworkCredential]::new('', $DbPassword).Password
az keyvault secret set `
    --vault-name $KeyVaultName `
    --name 'admcms-db-password' `
    --value $plain `
    --output none
Write-Host "  ✓ admcms-db-password set"

$allowlist = @{
    users = @($AllowlistUserOids)
    groups = @($AllowlistGroupOids)
} | ConvertTo-Json -Compress

az keyvault secret set `
    --vault-name $KeyVaultName `
    --name 'admcms-allowlist' `
    --value $allowlist `
    --output none
Write-Host "  ✓ admcms-allowlist set: $allowlist"

Write-Host ""
Write-Host "Done. Container App will pick these up on next restart." -ForegroundColor Green
