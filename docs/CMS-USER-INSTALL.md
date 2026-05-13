# ADMCMS in Claude — installatie voor eindgebruikers

Eenvoudige instructie voor CMS-medewerkers die de ADMCMS-database willen
gebruiken via Claude Desktop. < 5 minuten.

**Voorwaarden** (regelt jullie IT eenmalig):
- Jouw Microsoft-account zit in de group `SG-CMS-MCP-Users`
- Jouw IT heeft de Campai-app goedgekeurd in jullie tenant

---

## Stap 1 — Installeer Claude Desktop

Download op https://claude.ai/download → installeer → log in met je Anthropic
account (of maak er een gratis aan) → **open Claude Desktop minstens 1x**.

## Stap 2 — Voeg de ADMCMS-server toe

Open PowerShell (Windows-toets → "PowerShell" intypen → openen) en plak deze
regel:

```powershell
iwr https://raw.githubusercontent.com/Github-campai/AzureMCP-CMS/main/scripts/install-mcp-claude.ps1 | iex
```

Druk op Enter. Het script regelt alles, maakt een backup van je bestaande
Claude-config (mocht je die hebben), en bevestigt aan het eind: ✅ klaar.

## Stap 3 — Herstart Claude Desktop

**Volledig afsluiten** (rechtermuisklik op het Claude tray-icoon rechtsonder
→ **Quit**), dan opnieuw openen. Een normale window-X close is niet genoeg.

## Stap 4 — Eerste vraag

Begin een nieuw gesprek en typ bijvoorbeeld:

> *"Gebruik de admcms tool en haal de top 10 klanten van dit jaar op."*

Er opent een browser-popup → log in met je **Microsoft-account van CMS** →
klik **Accept** op de toestemming voor "Read ADMCMS data" → klaar.

Vanaf nu werkt het — vraag wat je wilt over verkoop, klanten, marges, voorraad,
leveringen.

---

## Voorbeeldvragen

- *"Wat zijn onze top 10 klanten dit jaar versus vorig jaar?"*
- *"Welke producten hebben verdachte prijzen?"*
- *"Wat is de marge per productgroep voor B2B in 2026?"*
- *"Toon recente orders van klant X."*
- *"Hoeveel verzendkosten hebben we het laatste kwartaal in rekening gebracht versus betaald?"*
- *"Welke producten verkochten we vorig jaar het meest in Duitsland?"*

## Werkt niet?

| Wat je ziet | Wat te doen |
|---|---|
| Script faalt op "Config niet gevonden" | Claude Desktop eerst 1x openen zodat de config wordt aangemaakt |
| "Connection refused" / "Server not reachable" | Check je internet |
| "Authorization failed" | Log uit en weer in bij Claude Desktop. Probeer opnieuw. |
| "User not on allowlist" | Vraag je IT om je in `SG-CMS-MCP-Users` te zetten. |
| "Admin consent required" | Vraag je IT om de éénmalige goedkeuring te geven. |
| Geen popup verschijnt | Check pop-up blocker in je browser. |

Hulp nodig? Stuur een mail naar **Janneke@campai.nl** met een screenshot van wat
je ziet.

---

## Handmatige installatie (als het script niet werkt)

Open **Claude Desktop → Settings → Developer → Edit Config**. Een bestand
`claude_desktop_config.json` opent. Voeg `mcpServers` toe **naast** de bestaande
inhoud:

```json
{
  "mcpServers": {
    "admcms": {
      "url": "https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp",
      "transport": "http"
    }
  }
}
```

Als er al andere keys staan (zoals `preferences`), voeg `mcpServers` toe als
extra key (let op de komma):

```json
{
  "preferences": { ...bestaande inhoud... },
  "mcpServers": {
    "admcms": {
      "url": "https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp",
      "transport": "http"
    }
  }
}
```

Bewaar, herstart Claude Desktop volledig (zie stap 3 hierboven).

## Alternatief: Cursor (voor developers)

Settings → MCP → Add server →
URL `https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp` →
type `http`.

## Alternatief: Claude Code (CLI)

```bash
claude mcp add admcms https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp --transport http
```

## Alternatief: Copilot Studio

Vereist een custom connector setup door jullie IT/Power Platform admin.
Configuratie staat in [`CLIENT-SETUP.md`](CLIENT-SETUP.md).
