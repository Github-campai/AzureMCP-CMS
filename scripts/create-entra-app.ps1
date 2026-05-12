# Create the multi-tenant Entra app registration for CMS-MCP-API.
# Requires Cloud Application Administrator or Application Administrator role in Campai tenant.
# Janneke runs this with her Global Admin login (separate from her daily account).
#
# Run:
#   az login --tenant bf030cec-d527-4f3c-9843-db5473bee3f7 --use-device-code
#   .\scripts\create-entra-app.ps1

[CmdletBinding()]
param(
    [Parameter()]
    [string]$DisplayName = 'CMS-MCP-API',

    [Parameter()]
    [string]$IdentifierUri = 'api://cms-mcp'
)

$ErrorActionPreference = 'Stop'

Write-Host "Creating Entra app registration '$DisplayName'..." -ForegroundColor Cyan

# Create the app (multi-tenant)
$app = az ad app create `
    --display-name $DisplayName `
    --sign-in-audience AzureADMultipleOrgs `
    --identifier-uris $IdentifierUri `
    -o json | ConvertFrom-Json

$appId = $app.appId
$objectId = $app.id

Write-Host "  App ID (client ID): $appId" -ForegroundColor Green
Write-Host "  Object ID:          $objectId"

# Define the admcms.read scope
$scopeId = [guid]::NewGuid().ToString()
$scopeBody = @{
    api = @{
        oauth2PermissionScopes = @(
            @{
                id = $scopeId
                adminConsentDescription = 'Allows the app to read ADMCMS data on behalf of the signed-in user'
                adminConsentDisplayName = 'Read ADMCMS data'
                userConsentDescription = 'Allow the app to read ADMCMS data on your behalf'
                userConsentDisplayName = 'Read ADMCMS data'
                value = 'admcms.read'
                type = 'User'
                isEnabled = $true
            }
        )
    }
} | ConvertTo-Json -Depth 10 -Compress

# Save to temp file because az ad app update needs a file for complex JSON
$tmp = New-TemporaryFile
$scopeBody | Out-File -FilePath $tmp -Encoding utf8
try {
    az rest --method PATCH `
        --uri "https://graph.microsoft.com/v1.0/applications/$objectId" `
        --headers "Content-Type=application/json" `
        --body "@$tmp" | Out-Null
} finally {
    Remove-Item $tmp -Force
}

Write-Host "  ✓ Scope 'admcms.read' added (id: $scopeId)" -ForegroundColor Green

# Create service principal in home tenant so the app can be granted access
az ad sp create --id $appId -o none 2>$null
Write-Host "  ✓ Service principal created in Campai tenant"

Write-Host ""
Write-Host "=== App Registration Created ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Set Container App env var ENTRA_APP_ID=$appId"
Write-Host "  2. Customer admin-consent URL:"
Write-Host "     https://login.microsoftonline.com/<CMS_TENANT_ID>/adminconsent?client_id=$appId"
Write-Host "  3. Update Container App env ENTRA_ALLOWED_TENANTS with CMS tenant ID"
Write-Host ""
Write-Host "  ENTRA_APP_ID=$appId"
