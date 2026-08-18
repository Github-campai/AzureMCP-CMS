<#
.SYNOPSIS
    Registers the MCP client callback URLs on the CMS-MCP-API app registration.

.DESCRIPTION
    The server proxies /authorize and /token to Entra without a client secret,
    so every client shares one Entra *public* client. Entra only accepts a
    redirect_uri that is registered on the app, and only accepts secret-less
    token redemption for redirect URIs on the publicClient platform ("Mobile and
    desktop applications"). Without these entries a user gets AADSTS50011 right
    after signing in.

    Existing redirect URIs are merged, never replaced - `az ad app update`
    overwrites the whole list, which would break the Copilot Studio connector
    that relies on the entries under the *web* platform.

    DRY RUN BY DEFAULT. Nothing changes until you pass -Uitvoeren.

.EXAMPLE
    .\scripts\add-client-redirect-uris.ps1
    Shows what would be added.

.EXAMPLE
    .\scripts\add-client-redirect-uris.ps1 -Uitvoeren
    Applies the change.
#>
[CmdletBinding()]
param(
    [string]$AppId = '1e6983d3-f996-4581-9518-e6819b79ac6a',

    # Set to actually write to Entra. Without it this script only reports.
    [switch]$Uitvoeren
)

$ErrorActionPreference = 'Stop'

# Callback URLs of the clients CMS users actually use. Entra matches loopback
# redirect URIs for public clients ignoring the port, so one http://localhost
# entry covers Claude Code, mcp-remote, Cursor and VS Code.
$Gewenst = @(
    'https://claude.ai/api/mcp/auth_callback'
    'https://claude.com/api/mcp/auth_callback'
    'http://localhost'
    'http://127.0.0.1'
    'https://vscode.dev/redirect'
    'cursor://anysphere.cursor-mcp/oauth/callback'
)

Write-Host "App registration: $AppId" -ForegroundColor Cyan

$app = az ad app show --id $AppId --query "{name:displayName, publicClient:publicClient.redirectUris, web:web.redirectUris, spa:spa.redirectUris}" -o json | ConvertFrom-Json
if (-not $app) { throw "Kon app registration $AppId niet ophalen. Eerst 'az login'?" }

$huidig = @($app.publicClient)
Write-Host "`nNaam:                      $($app.name)"
$huidigTekst = if ($huidig.Count) { $huidig -join ', ' } else { '<geen>' }
Write-Host "Huidige publicClient URIs: $huidigTekst"
Write-Host "Web URIs (blijven staan):  $($app.web -join ', ')"

$toeTeVoegen = $Gewenst | Where-Object { $_ -notin $huidig }
if (-not $toeTeVoegen) {
    Write-Host "`nAlles staat er al. Geen wijziging nodig." -ForegroundColor Green
    return
}

Write-Host "`nToe te voegen onder publicClient:" -ForegroundColor Yellow
$toeTeVoegen | ForEach-Object { Write-Host "  + $_" }

$nieuw = @($huidig + $toeTeVoegen | Select-Object -Unique)

if (-not $Uitvoeren) {
    Write-Host "`n== DRY RUN - er is niets gewijzigd ==" -ForegroundColor Yellow
    Write-Host "Uitvoeren met:  .\scripts\add-client-redirect-uris.ps1 -Uitvoeren"
    Write-Host "`nTerugdraaien na een echte run:"
    $rollback = if ($huidig.Count) { $huidig -join ' ' } else { '<leeg -- verwijder alle publicClient URIs>' }
    Write-Host "  az ad app update --id $AppId --public-client-redirect-uris $rollback"
    return
}

Write-Host "`nWijzigen..." -ForegroundColor Cyan
az ad app update --id $AppId --public-client-redirect-uris @nieuw
if ($LASTEXITCODE -ne 0) { throw 'az ad app update is mislukt.' }

$na = az ad app show --id $AppId --query "publicClient.redirectUris" -o json | ConvertFrom-Json
Write-Host "`nPublicClient URIs nu:" -ForegroundColor Green
$na | ForEach-Object { Write-Host "  - $_" }

$ontbreekt = $Gewenst | Where-Object { $_ -notin @($na) }
if ($ontbreekt) {
    Write-Warning "Niet doorgekomen: $($ontbreekt -join ', ')"
    exit 1
}
Write-Host "`nKlaar. Clients kunnen nu inloggen met alleen de MCP-URL." -ForegroundColor Green
