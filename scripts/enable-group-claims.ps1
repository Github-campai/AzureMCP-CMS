<#
.SYNOPSIS
    Laat toegang via Entra-groepen werken in plaats van losse OID's in Key Vault.

.DESCRIPTION
    De allowlist in Key Vault bevat twee groepen, maar die doen niets: de
    app-registratie heeft `groupMembershipClaims = null`, dus Entra stuurt geen
    `groups`-claim mee. `check_access` doet dan `set([]) & set(groepen)` en dat
    matcht nooit. Gevolg: elke gebruiker moet handmatig als OID in Key Vault en
    CMS' IT kan niemand zelf toevoegen.

    Waarom ApplicationGroup en niet SecurityGroup:
      SecurityGroup zet ALLE groepen van een gebruiker in het token. Boven ~150
      groepen kapt Entra dat af tot een verwijzing (`_claim_names`), en dan werkt
      groepstoegang stil niet meer -- precies het soort storing dat je pas maanden
      later ontdekt. ApplicationGroup stuurt alleen de groepen mee die aan DEZE
      applicatie zijn toegewezen. Geen afkapping, en het geeft de klant
      zelfbediening: hun beheerder wijst in hun eigen tenant hun eigen groep toe
      aan de enterprise app.

    Aan de servercode verandert niets. De bestaande groepstak in check_access
    begint simpelweg te werken zodra de claim binnenkomt.

    DRY RUN, TENZIJ -Uitvoeren.

.EXAMPLE
    .\scripts\enable-group-claims.ps1
.EXAMPLE
    .\scripts\enable-group-claims.ps1 -Uitvoeren
#>
[CmdletBinding()]
param(
    [switch]$Uitvoeren,

    [string]$AppId = '1e6983d3-f996-4581-9518-e6819b79ac6a',

    # Groep in de Campai-tenant die toegang moet geven.
    [string]$CampaiGroupId = 'bd6ce8cc-033a-482e-a7cd-acd145e6d93f',

    # Groep in de CMS-tenant. Staat al op de allowlist; toewijzen moet in HUN
    # tenant gebeuren (door hun beheerder, of door Campai via GDAP).
    [string]$CmsGroupId = '68f3ad86-6967-4540-92e5-b8b425ca720d',

    [string]$KeyVault = 'cms-mcp-kv-prod',
    [string]$McpSubscription = '1886dd8f-1ce7-4af2-8491-338341544b55'
)

$ErrorActionPreference = 'Stop'

function Stap($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "   OK   $t" -ForegroundColor Green }
function Waarschuwing($t) { Write-Host "   LET OP  $t" -ForegroundColor Yellow }

# Rol-id voor "Default Access" -- toewijzen zonder een specifieke app-rol te geven.
$DefaultAccessRole = '00000000-0000-0000-0000-000000000000'

Stap 'Preflight'
$account = az account show -o json 2>$null | ConvertFrom-Json
if (-not $account) { throw 'Niet ingelogd. Doe eerst: az login' }
Ok "Ingelogd als $($account.user.name) in tenant $($account.tenantId)"

$app = az ad app show --id $AppId -o json | ConvertFrom-Json
if (-not $app) { throw "App-registratie $AppId niet gevonden." }
Ok "App: $($app.displayName)  (huidige groupMembershipClaims: $(if ($app.groupMembershipClaims) { $app.groupMembershipClaims } else { '<null -- geen groups-claim>' }))"

$spId = az ad sp show --id $AppId --query id -o tsv
Ok "Enterprise app (service principal): $spId"

# Staan de groepen ook echt op de allowlist? Zonder dat heeft de claim geen effect.
Stap 'Allowlist controleren'
$allowlist = az keyvault secret show --vault-name $KeyVault --name admcms-allowlist `
    --subscription $McpSubscription --query value -o tsv | ConvertFrom-Json
$opAllowlist = @($allowlist.groups)
foreach ($g in @($CampaiGroupId, $CmsGroupId)) {
    if ($opAllowlist -contains $g) { Ok "groep $g staat op de allowlist" }
    else { Waarschuwing "groep $g staat NIET op de allowlist -- toewijzen heeft dan geen effect" }
}

Stap 'Huidige toewijzingen aan de enterprise app'
$assigned = az rest --method GET --url "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo" -o json | ConvertFrom-Json
foreach ($a in $assigned.value) { Write-Host "   - $($a.principalType): $($a.principalDisplayName)" }
$campaiToegewezen = @($assigned.value | Where-Object { $_.principalId -eq $CampaiGroupId }).Count -gt 0

Stap 'Plan'
$claimWijzigt = $app.groupMembershipClaims -ne 'ApplicationGroup'
if ($claimWijzigt) { Write-Host "   1. groupMembershipClaims: '$($app.groupMembershipClaims)' -> 'ApplicationGroup'" }
else { Write-Host "   1. groupMembershipClaims staat al op ApplicationGroup" }
if (-not $campaiToegewezen) {
    $groepNaam = az ad group show --group $CampaiGroupId --query displayName -o tsv 2>$null
    Write-Host "   2. groep toewijzen aan de app: $groepNaam ($CampaiGroupId)"
}
else { Write-Host "   2. Campai-groep is al toegewezen" }

if (-not $Uitvoeren) {
    Write-Host "`n== DRY RUN - er is niets gewijzigd ==" -ForegroundColor Yellow
    Write-Host "Uitvoeren met:  .\scripts\enable-group-claims.ps1 -Uitvoeren"
    Write-Host "`nTerugdraaien na een echte run:"
    Write-Host "  az ad app update --id $AppId --set groupMembershipClaims=null"
    Write-Host "`nNog te doen in de CMS-tenant (hun beheerder, of Campai via GDAP):"
    Write-Host "  Enterprise applications -> CMS-MCP-API -> Users and groups -> groep $CmsGroupId toewijzen"
    return
}

if ($claimWijzigt) {
    Stap 'groupMembershipClaims zetten'
    az ad app update --id $AppId --set groupMembershipClaims=ApplicationGroup
    if ($LASTEXITCODE -ne 0) { throw 'az ad app update is mislukt.' }
    Ok 'groupMembershipClaims = ApplicationGroup'
}

if (-not $campaiToegewezen) {
    Stap 'Campai-groep toewijzen aan de enterprise app'
    $body = @{ principalId = $CampaiGroupId; resourceId = $spId; appRoleId = $DefaultAccessRole } | ConvertTo-Json -Compress
    $tmp = Join-Path $env:TEMP 'groupassign.json'
    Set-Content -Path $tmp -Value $body -Encoding utf8
    az rest --method POST --url "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo" `
        --headers 'Content-Type=application/json' --body "@$tmp" --output none
    Remove-Item $tmp -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) { throw 'Toewijzen van de groep is mislukt.' }
    Ok 'Groep toegewezen'
}

Stap 'Verifieren'
$na = az ad app show --id $AppId --query groupMembershipClaims -o tsv
Write-Host "   groupMembershipClaims: $na"
$naAssigned = az rest --method GET --url "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo" -o json | ConvertFrom-Json
foreach ($a in $naAssigned.value) { Write-Host "   - $($a.principalType): $($a.principalDisplayName)" }

Write-Host "`n== KLAAR ==" -ForegroundColor Green
Write-Host @"

Nog twee dingen voordat dit echt werkt:

1. CMS-tenant: hun beheerder (of Campai via GDAP) wijst hun groep toe aan de
   enterprise app 'CMS-MCP-API':
     Entra -> Enterprise applications -> CMS-MCP-API -> Users and groups -> Add
     groep-id: $CmsGroupId

2. Iedereen moet EEN KEER opnieuw inloggen. Een bestaand access token bevat de
   nieuwe claim niet; die komt pas bij een nieuwe authorisatie.

Controleren of de claim binnenkomt (na opnieuw inloggen), een regel:
  az account get-access-token --scope "api://$AppId/admcms.read" --query accessToken -o tsv | % { `$p = `$_.Split('.')[1].Replace('-','+').Replace('_','/'); `$p += '=' * ((4 - `$p.Length % 4) % 4); (`[Text.Encoding]::UTF8.GetString(`[Convert]::FromBase64String(`$p)) | ConvertFrom-Json).groups }

Zodra dat groep-id's teruggeeft, kunnen de losse OID's uit de allowlist -- op de
breakglass-account na, die je wilt houden voor als groepstoegang ooit faalt.
"@
