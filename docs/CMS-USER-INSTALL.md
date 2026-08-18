# ADMCMS in Claude — installatie voor eindgebruikers

Eenvoudige instructie voor CMS-medewerkers die de ADMCMS-database willen
gebruiken via Claude. < 2 minuten.

**Voorwaarden** (regelt jullie IT eenmalig):
- Jouw Microsoft-account zit in de group `SG-CMS-MCP-Users`
- Jouw IT heeft de Campai-app goedgekeurd in jullie tenant

---

## De URL

```
https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp
```

Dat is alles wat je nodig hebt. Geen script, geen client-ID, geen configuratie-
bestand: de server vertelt je AI-tool zelf hoe het inloggen werkt.

## Claude (web of desktop)

1. Ga naar **Settings → Connectors → Add custom connector**
2. Plak de URL hierboven
3. Klik **Add** → er opent een Microsoft-inlogvenster
4. Log in met je **CMS-account** en klik **Accept** op "Read ADMCMS data"

Klaar. Vraag daarna bijvoorbeeld: *"Wat zijn onze top 10 klanten dit jaar?"*

## Claude Code (CLI)

```bash
claude mcp add --transport http admcms https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp
```

De eerste tool-aanroep opent de browser voor het inloggen.

## Cursor

Settings → MCP → **Add new MCP server** → type `http` → URL erin. Bij de eerste
call volgt de inlogflow.

## VS Code

Settings → MCP → Servers → toevoegen:

```json
{
  "admcms": {
    "type": "http",
    "url": "https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp"
  }
}
```

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
| "Connection refused" / "Server not reachable" | Check je internet |
| `AADSTS50011` / "redirect URI mismatch" | Meld dit bij Campai — de callback van jouw client staat nog niet in de app-registratie |
| "Admin consent required" | Vraag je IT om de éénmalige goedkeuring te geven |
| "User not on allowlist" (403) | Vraag je IT om je in `SG-CMS-MCP-Users` te zetten |
| "Authorization failed" na een uur | Log opnieuw in; normaal vernieuwt de sessie zichzelf |
| Geen inlogvenster | Check je pop-up blocker |

Hulp nodig? Stuur een mail naar **Janneke@campai.nl** met een screenshot van wat
je ziet.

## Copilot Studio

Vereist een custom connector door jullie Power Platform admin — zie
[`CLIENT-SETUP.md`](CLIENT-SETUP.md).
