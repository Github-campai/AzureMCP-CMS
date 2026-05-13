# Installs the ADMCMS MCP server in Claude Desktop's config.
#
# Safe to re-run: merges into existing config, preserves other settings.
#
# One-line install (PowerShell):
#   iwr https://raw.githubusercontent.com/Github-campai/AzureMCP-CMS/main/scripts/install-mcp-claude.ps1 | iex
#
# Or download and inspect first:
#   curl.exe -o install-mcp-claude.ps1 https://raw.githubusercontent.com/Github-campai/AzureMCP-CMS/main/scripts/install-mcp-claude.ps1
#   .\install-mcp-claude.ps1

[CmdletBinding()]
param(
    [string]$ServerName = 'admcms',
    [string]$ServerUrl = 'https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp'
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }

Write-Step "ADMCMS MCP installer voor Claude Desktop"
Write-Host "    Server: $ServerUrl"
Write-Host "    Naam:   $ServerName"

# 1. Locate config file
$configDir = Join-Path $env:APPDATA 'Claude'
$configPath = Join-Path $configDir 'claude_desktop_config.json'

Write-Step "Config locatie zoeken"
if (-not (Test-Path $configDir)) {
    Write-Warn "Claude Desktop config-folder niet gevonden ($configDir)."
    Write-Warn "Installeer eerst Claude Desktop via https://claude.ai/download"
    Write-Warn "en open hem minstens 1x voordat je dit script draait."
    exit 1
}
Write-Ok "Config-folder gevonden: $configDir"

# 2. Read or create config
if (Test-Path $configPath) {
    Write-Step "Bestaande config lezen"
    try {
        $config = Get-Content $configPath -Raw | ConvertFrom-Json -AsHashtable
    } catch {
        Write-Warn "Config-bestand is geen geldige JSON: $_"
        Write-Warn "Maak een backup en verwijder/repareer het bestand. Pad: $configPath"
        exit 1
    }
    # Backup voor de zekerheid
    $backupPath = "$configPath.bak-$(Get-Date -Format 'yyyyMMddHHmmss')"
    Copy-Item $configPath $backupPath
    Write-Ok "Backup gemaakt: $backupPath"
} else {
    Write-Step "Geen bestaande config; maak nieuwe"
    $config = @{}
}

# 3. Ensure mcpServers key exists
if (-not $config.ContainsKey('mcpServers')) {
    $config['mcpServers'] = @{}
}

# 4. Add/update our server entry
$existing = $config['mcpServers'][$ServerName]
if ($existing) {
    Write-Warn "MCP server '$ServerName' bestond al; wordt overschreven."
}

$config['mcpServers'][$ServerName] = [ordered]@{
    url       = $ServerUrl
    transport = 'http'
}

# 5. Write back
$json = $config | ConvertTo-Json -Depth 20
Set-Content -Path $configPath -Value $json -Encoding UTF8
Write-Ok "Config bijgewerkt: $configPath"

# 6. Final instructions
Write-Step "Klaar!"
Write-Host ""
Write-Host "Volgende stappen:" -ForegroundColor White
Write-Host "  1. Sluit Claude Desktop volledig af (rechter muisklik op tray-icoon -> Quit)"
Write-Host "  2. Start Claude Desktop opnieuw"
Write-Host "  3. Begin een nieuw gesprek en vraag:"
Write-Host "     ""Gebruik admcms en haal de top 10 klanten van dit jaar op."""
Write-Host "  4. Een browser-popup vraagt om Microsoft-login -> log in -> Accept"
Write-Host ""
Write-Host "Problemen? Stuur een mail naar Janneke@campai.nl"
Write-Host ""
