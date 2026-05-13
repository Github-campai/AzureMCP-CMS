# ADMCMS in Claude — installatie voor eindgebruikers

Eenvoudige instructie voor CMS-medewerkers die de ADMCMS-database willen
gebruiken via Claude Desktop. Drie stappen, < 5 minuten.

**Voorwaarden** (regelt jullie IT eenmalig):
- Jouw Microsoft-account zit in de group `SG-CMS-MCP-Users`
- Jouw IT heeft de Campai-app goedgekeurd in jullie tenant

---

## Stap 1 — Installeer Claude Desktop

Download op https://claude.ai/download → installeer → log in met je Anthropic
account (of maak er een gratis aan).

## Stap 2 — Voeg de ADMCMS-server toe

Open Claude Desktop → klik linksboven op **☰** (menu) → **Settings** →
**Developer** → klik **Edit Config**.

Een tekstbestand opent. Vervang de hele inhoud met:

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

Bewaar het bestand (`Ctrl+S`) en sluit.

## Stap 3 — Herstart Claude Desktop

Helemaal afsluiten en opnieuw openen.

## Stap 4 — Eerste vraag

Begin een nieuw gesprek en typ bijvoorbeeld:

> *"Gebruik de admcms tool en haal de top 10 klanten van dit jaar op."*

Er opent een browser-popup → log in met je **CMS Microsoft-account** → klik
**Accept** op de toestemming voor "Read ADMCMS data" → klaar.

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
| "Connection refused" / "Server not reachable" | Check je internet. Kopieer de URL nogmaals exact. |
| "Authorization failed" | Log uit en weer in bij Claude Desktop. Probeer opnieuw. |
| "User not on allowlist" | Vraag je IT om je in `SG-CMS-MCP-Users` te zetten. |
| "Admin consent required" | Vraag je IT om de éénmalige goedkeuring te geven. |
| Geen popup verschijnt | Check pop-up blocker in je browser. |

Hulp nodig? Stuur een mail naar **Janneke@campai.nl** met een screenshot van wat
je ziet.

---

## Alternatief: Cursor (voor developers)

Settings → MCP → Add server →
URL `https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp` →
type `http`.

## Alternatief: Copilot Studio (voor agents)

Vraag bij je IT — vereist een custom connector setup, niet zelf-installeerbaar.
Configuratie staat in [`CLIENT-SETUP.md`](CLIENT-SETUP.md).
