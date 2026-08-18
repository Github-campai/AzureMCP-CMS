<#
.SYNOPSIS
    Geeft de MCP een eigen read-only SQL-login en zet die in Key Vault.

.DESCRIPTION
    De MCP verbindt nu als 'saazuredb' -- de server-administrator van cmsnl, die
    vier databases host. Voor 21 read-only tools is db_datareader op een enkele
    database genoeg. Dit script:

      1. maakt (of hergebruikt) de login 'admcms_mcp' op master
      2. maakt de user aan op ADMCMS-PRODUCTION-REPL01 met db_datareader
      3. controleert de nieuwe login door er zelf mee in te loggen
      4. zet username + password in Key Vault (pas na die controle)
      5. herstart de actieve Container App-revisie
      6. verifieert end-to-end via de MCP-tool health_check

    Het huidige admin-wachtwoord wordt uit Key Vault gelezen om de login te
    kunnen aanmaken; het wordt nergens getoond of weggeschreven.

    DRY RUN, TENZIJ -Uitvoeren. Zonder die switch verandert er niets.

    LET OP: dit maakt het gelekte admin-wachtwoord nog niet ongeldig. Dat blijft
    een aparte stap met de leverancier -- zie de uitvoer aan het eind.

.EXAMPLE
    .\scripts\rotate-db-credentials.ps1
    Laat zien wat er zou gebeuren.

.EXAMPLE
    .\scripts\rotate-db-credentials.ps1 -Uitvoeren
    Vraagt om een wachtwoord (of genereert er een) en voert alles uit.
#>
[CmdletBinding()]
param(
    # Zet dit om echt te wijzigen. Zonder deze switch: alleen rapporteren.
    [switch]$Uitvoeren,

    # Sla de wachtwoordvraag over en genereer er een. Het wachtwoord wordt dan
    # alleen naar Key Vault geschreven en nergens getoond -- niemand hoeft het te
    # kennen, want de app haalt het zelf uit de vault.
    [switch]$GenereerWachtwoord,

    [string]$LoginName = 'admcms_mcp',

    [string]$SqlServer = 'cmsnl.database.windows.net',
    [string]$Database = 'ADMCMS-PRODUCTION-REPL01',

    [string]$KeyVault = 'cms-mcp-kv-prod',
    [string]$ContainerApp = 'cms-mcp-app-prod',
    [string]$ResourceGroup = 'CMS-MCP-rg-prod',
    [string]$McpSubscription = '1886dd8f-1ce7-4af2-8491-338341544b55',
    [string]$SqlSubscription = '176aa320-3340-49ca-ae1d-019dffd44bf8',
    [string]$SqlResourceGroup = 'MSSQL-REP',
    [string]$SqlServerName = 'cmsnl',
    [string]$EntraAppId = '1e6983d3-f996-4581-9518-e6819b79ac6a'
)

$ErrorActionPreference = 'Stop'

function Stap($tekst) { Write-Host "`n== $tekst" -ForegroundColor Cyan }
function Ok($tekst) { Write-Host "   OK   $tekst" -ForegroundColor Green }
function Waarschuwing($tekst) { Write-Host "   LET OP  $tekst" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

Stap 'Preflight'

$account = az account show -o json 2>$null | ConvertFrom-Json
if (-not $account) { throw "Niet ingelogd. Doe eerst: az login" }
Ok "Ingelogd als $($account.user.name)"

$sqlcmd = (Get-Command sqlcmd -ErrorAction SilentlyContinue).Source
if (-not $sqlcmd) {
    $kandidaten = @(
        'C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\170\Tools\Binn\SQLCMD.EXE'
        'C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\180\Tools\Binn\SQLCMD.EXE'
    )
    $sqlcmd = $kandidaten | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $sqlcmd) { throw "sqlcmd niet gevonden. Installeer de 'Microsoft ODBC Driver for SQL Server' command line utilities." }
Ok "sqlcmd: $sqlcmd"

# Kan ik bij het secret? (alleen de id opvragen, nooit de waarde loggen)
$secretId = az keyvault secret show --vault-name $KeyVault --name admcms-db-password `
    --subscription $McpSubscription --query id -o tsv 2>$null
if (-not $secretId) { throw "Geen leesrechten op $KeyVault / admcms-db-password (of vault onbereikbaar)." }
Ok "Key Vault leesbaar"

# Staat mijn IP in de SQL-firewall? Zo niet, dan hangt de sqlcmd-verbinding.
try { $mijnIp = (Invoke-RestMethod 'https://api.ipify.org?format=json' -TimeoutSec 10).ip } catch { $mijnIp = $null }
if ($mijnIp) {
    $regels = az sql server firewall-rule list --server $SqlServerName --resource-group $SqlResourceGroup `
        --subscription $SqlSubscription -o json | ConvertFrom-Json
    $toegestaan = $regels | Where-Object {
        ([version]::TryParse($_.startIpAddress, [ref]$null)) -and
        ([System.Net.IPAddress]::Parse($mijnIp).Address -ge [System.Net.IPAddress]::Parse($_.startIpAddress).Address) -and
        ([System.Net.IPAddress]::Parse($mijnIp).Address -le [System.Net.IPAddress]::Parse($_.endIpAddress).Address)
    }
    if ($toegestaan) {
        Ok "Jouw IP $mijnIp valt onder firewall-regel '$($toegestaan[0].name)'"
    }
    else {
        Waarschuwing "Jouw IP $mijnIp staat NIET in de firewall van $SqlServerName."
        Write-Host "            De verbinding zal dan mislukken. Regel toevoegen (aparte, bewuste actie):"
        Write-Host "            az sql server firewall-rule create --server $SqlServerName --resource-group $SqlResourceGroup ``"
        Write-Host "              --subscription $SqlSubscription --name 'Janneke-rotatie' ``"
        Write-Host "              --start-ip-address $mijnIp --end-ip-address $mijnIp"
    }
}

# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

Stap 'Plan'
Write-Host "   Server        : $SqlServer"
Write-Host "   Database      : $Database"
Write-Host "   Nieuwe login  : $LoginName  (db_datareader, alleen deze database)"
Write-Host "   Key Vault     : $KeyVault  -> admcms-db-username, admcms-db-password"
Write-Host "   Container App : $ContainerApp (herstart van de actieve revisie)"

if (-not $Uitvoeren) {
    Write-Host "`n== DRY RUN - er is niets gewijzigd ==" -ForegroundColor Yellow
    Write-Host "Uitvoeren met:  .\scripts\rotate-db-credentials.ps1 -Uitvoeren"
    Write-Host "`nTerugdraaien kan altijd: Key Vault houdt de vorige secret-versies vast."
    Write-Host "  az keyvault secret list-versions --vault-name $KeyVault --name admcms-db-password -o table"
    return
}

# ---------------------------------------------------------------------------
# Wachtwoord
# ---------------------------------------------------------------------------

Stap 'Nieuw wachtwoord'
if ($GenereerWachtwoord) {
    $nieuwWachtwoord = ''
    Write-Host "   -GenereerWachtwoord: wachtwoord wordt gegenereerd, geen invoer nodig."
}
else {
    Write-Host "   Plak een wachtwoord en druk op Enter (je ziet niets tijdens het plakken),"
    Write-Host "   of druk direct op Enter om er een te laten genereren."
    $secure = Read-Host -Prompt '   Wachtwoord' -AsSecureString
    $nieuwWachtwoord = [System.Net.NetworkCredential]::new('', $secure).Password
}

if ([string]::IsNullOrWhiteSpace($nieuwWachtwoord)) {
    $bytes = [byte[]]::new(24)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $nieuwWachtwoord = ([Convert]::ToBase64String($bytes) -replace "[+/=']", 'x')
    Ok "Wachtwoord gegenereerd ($($nieuwWachtwoord.Length) tekens). Het staat straks in Key Vault."
}
else {
    if ($nieuwWachtwoord.Length -lt 16) { throw "Te kort: gebruik minimaal 16 tekens (Azure SQL eist 8, dit is een productie-login)." }
    if ($nieuwWachtwoord -match "'") { throw "Gebruik geen apostrof (') in het wachtwoord -- dat breekt de SQL-statements." }
    Ok "Wachtwoord geaccepteerd ($($nieuwWachtwoord.Length) tekens)"
}

# ---------------------------------------------------------------------------
# Huidige admin-credentials uit Key Vault (blijven in het geheugen)
# ---------------------------------------------------------------------------

Stap 'Huidige credentials ophalen uit Key Vault'
$adminUser = az keyvault secret show --vault-name $KeyVault --name admcms-db-username `
    --subscription $McpSubscription --query value -o tsv
$adminPassword = az keyvault secret show --vault-name $KeyVault --name admcms-db-password `
    --subscription $McpSubscription --query value -o tsv
if (-not $adminUser -or -not $adminPassword) { throw 'Kon de huidige credentials niet lezen.' }
Ok "Verbinden als '$adminUser' (wachtwoord niet getoond)"

function Invoke-Sql {
    param([string]$Db, [string]$Sql, [string]$User, [string]$Password)
    $env:SQLCMDPASSWORD = $Password   # niet via -P: dat zet het op de commandline
    try {
        $uit = & $sqlcmd -S $SqlServer -d $Db -U $User -N -l 30 -b -h -1 -W -Q $Sql 2>&1
        if ($LASTEXITCODE -ne 0) { throw ("sqlcmd faalde: " + ($uit -join ' ')) }
        return ($uit | Where-Object { $_ -and $_ -notmatch '^\s*$' })
    }
    finally { Remove-Item Env:SQLCMDPASSWORD -ErrorAction SilentlyContinue }
}

$pwSql = $nieuwWachtwoord -replace "'", "''"

# ---------------------------------------------------------------------------
# Login + user aanmaken
# ---------------------------------------------------------------------------

Stap "Login '$LoginName' aanmaken of bijwerken (master)"
$sqlLogin = @"
IF EXISTS (SELECT 1 FROM sys.sql_logins WHERE name = '$LoginName')
    ALTER LOGIN [$LoginName] WITH PASSWORD = '$pwSql';
ELSE
    CREATE LOGIN [$LoginName] WITH PASSWORD = '$pwSql';
SELECT 'login-ok';
"@
Invoke-Sql -Db 'master' -Sql $sqlLogin -User $adminUser -Password $adminPassword | Out-Null
Ok 'Login staat klaar'

Stap "User + db_datareader op $Database"
$sqlUser = @"
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = '$LoginName')
    CREATE USER [$LoginName] FOR LOGIN [$LoginName];
IF IS_ROLEMEMBER('db_datareader', '$LoginName') = 0
    ALTER ROLE db_datareader ADD MEMBER [$LoginName];
SELECT 'user-ok';
"@
Invoke-Sql -Db $Database -Sql $sqlUser -User $adminUser -Password $adminPassword | Out-Null
Ok 'User staat in db_datareader'

# ---------------------------------------------------------------------------
# Nieuwe login controleren VOORDAT Key Vault wijzigt
# ---------------------------------------------------------------------------

Stap 'Nieuwe login uitproberen'
$check = Invoke-Sql -Db $Database -User $LoginName -Password $nieuwWachtwoord -Sql @"
SELECT CONCAT('rolemember=', IS_ROLEMEMBER('db_datareader'), ';tables=', (SELECT COUNT(*) FROM sys.objects WHERE type = 'U'));
"@
$resultaat = ($check -join ' ').Trim()
if ($resultaat -notmatch 'rolemember=1') { throw "Nieuwe login heeft geen db_datareader: $resultaat" }
Ok "Werkt: $resultaat"

# ---------------------------------------------------------------------------
# Key Vault bijwerken
# ---------------------------------------------------------------------------

Stap 'Key Vault bijwerken'
az keyvault secret set --vault-name $KeyVault --name admcms-db-username `
    --value $LoginName --subscription $McpSubscription --output none
az keyvault secret set --vault-name $KeyVault --name admcms-db-password `
    --value $nieuwWachtwoord --subscription $McpSubscription --output none
Ok 'admcms-db-username en admcms-db-password bijgewerkt (vorige versies blijven bewaard)'

# ---------------------------------------------------------------------------
# App herstarten -- de connectiepool houdt oude credentials vast
# ---------------------------------------------------------------------------

Stap 'Container App herstarten'
$revisie = az containerapp revision list -n $ContainerApp -g $ResourceGroup `
    --subscription $McpSubscription --query "[?properties.active].name | [0]" -o tsv
az containerapp revision restart -n $ContainerApp -g $ResourceGroup `
    --revision $revisie --subscription $McpSubscription --output none
Ok "Revisie $revisie herstart"

# ---------------------------------------------------------------------------
# End-to-end verificatie via de MCP
# ---------------------------------------------------------------------------

Stap 'Verifieren via de MCP-tool health_check'
$fqdn = az containerapp show -n $ContainerApp -g $ResourceGroup --subscription $McpSubscription `
    --query "properties.configuration.ingress.fqdn" -o tsv
$token = az account get-access-token --scope "api://$EntraAppId/admcms.read" --query accessToken -o tsv

$headers = @{ Authorization = "Bearer $token"; Accept = 'application/json, text/event-stream' }
$init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"rotate-script","version":"1"}}}'
$call = '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"health_check","arguments":{}}}'

$dbStatus = $null
foreach ($poging in 1..6) {
    try {
        Invoke-WebRequest -Uri "https://$fqdn/mcp" -Method POST -Headers $headers `
            -ContentType 'application/json' -Body $init -TimeoutSec 60 | Out-Null
        $resp = Invoke-WebRequest -Uri "https://$fqdn/mcp" -Method POST -Headers $headers `
            -ContentType 'application/json' -Body $call -TimeoutSec 180
        $payload = ($resp.Content -split 'data: ')[-1] | ConvertFrom-Json
        $health = $payload.result.content[0].text | ConvertFrom-Json
        $dbStatus = $health.database.status
        if ($dbStatus -eq 'ok') { break }
        Waarschuwing "database.status = $dbStatus (poging $poging/6)"
    }
    catch {
        Waarschuwing "poging $poging/6 mislukt: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 10
}

# Geheugen opruimen
$nieuwWachtwoord = $null; $adminPassword = $null; $pwSql = $null; $token = $null
[System.GC]::Collect()

if ($dbStatus -ne 'ok') {
    Write-Host "`n== NIET AF ==" -ForegroundColor Red
    Write-Host "De MCP kreeg geen werkende databaseverbinding. Terugrollen:"
    Write-Host "  az keyvault secret list-versions --vault-name $KeyVault --name admcms-db-password -o table"
    Write-Host "  az keyvault secret set-attributes --vault-name $KeyVault --name admcms-db-password --version <vorige> --enabled true"
    Write-Host "  (zelfde voor admcms-db-username, daarna de revisie opnieuw herstarten)"
    Write-Host "  Logs: az containerapp logs show -n $ContainerApp -g $ResourceGroup --follow"
    exit 1
}

Write-Host "`n== KLAAR ==" -ForegroundColor Green
Ok "MCP draait op de nieuwe read-only login, database.status = ok"

Write-Host "`nNog te doen -- dit script maakt het gelekte wachtwoord NIET ongeldig:" -ForegroundColor Yellow
Write-Host "  Het wachtwoord van 'saazuredb' (server-admin van $SqlServerName, 4 databases) staat nog"
Write-Host "  in de git-historie en is nog geldig. Roteren gaat in overleg met de leverancier,"
Write-Host "  omdat die login volgens docs/service-handleiding.md leveranciersgegeven is:"
Write-Host ""
Write-Host "    az sql server update --name $SqlServerName --resource-group $SqlResourceGroup ``"
Write-Host "      --subscription $SqlSubscription --admin-password <nieuw>"
Write-Host ""
Write-Host "  De MCP heeft die login na vandaag niet meer nodig."
