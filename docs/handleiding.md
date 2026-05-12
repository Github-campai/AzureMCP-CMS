# ADMCMS MCP Server — Installatie & Gebruikshandleiding

> **⚠️ LET OP — Deze handleiding beschrijft de originele lokale stdio-modus.**
>
> De huidige codebase ondersteunt alleen de **remote HTTP mode** (geen stdio meer). Deze handleiding is bewaard als referentie voor de achterliggende concepten (ADMCMS schema, use cases, voorbeelden), maar de installatiestappen (Claude Desktop JSON config met `command: "python"`) zijn verouderd.
>
> Voor de nieuwe remote setup zie straks `docs/deployment.md` (Azure deployment) en `README.md` (quick start).

---

## Wat is dit?

De ADMCMS MCP Server is een tool die Claude (AI) in staat stelt om direct vragen te beantwoorden over de ADMCMS database. In plaats van een PowerBI developer te vragen om elke keer een nieuw rapport te maken, kun je gewoon in natuurlijke taal vragen stellen:

- *"Wat is de omzet van deze maand vergeleken met vorig jaar?"*
- *"Welke 20 producten verkopen het beste dit kwartaal?"*
- *"Hoeveel openstaande orders zijn er voor klant X?"*
- *"Wat is de voorraadwaarde per warehouse?"*

Claude genereert de juiste SQL query, voert die uit op de readonly Azure SQL replica, en geeft het antwoord terug.

**Tijdsinschatting installatie:** 15-20 minuten

---

## Overzicht

```
                 ┌──────────────┐
    Gebruiker →  │  Claude.ai   │  ← Vraag in natuurlijke taal
                 │  of Claude   │
                 │  Desktop     │
                 └──────┬───────┘
                        │ MCP Protocol
                 ┌──────▼───────┐
                 │  ADMCMS MCP  │  ← Server op je PC
                 │  Server      │
                 │  (Python)    │
                 └──────┬───────┘
                        │ SQL (readonly)
                 ┌──────▼───────┐
                 │  Azure SQL   │  ← Replicatie database
                 │  Replica     │
                 └──────────────┘
```

---

## Vereisten (checklist)

Voordat je begint, controleer of je het volgende hebt:

- [ ] **Python 3.10+** geïnstalleerd (met `pip`)
- [ ] **ODBC Driver 18 for SQL Server** geïnstalleerd
- [ ] **pyodbc** Python package geïnstalleerd (`pip install pyodbc`)
- [ ] **Werkende verbinding** naar `cmsnl.database.windows.net` (eerder getest met het discovery script)
- [ ] **Claude Desktop app** geïnstalleerd (https://claude.ai/download)

Als Python en de ODBC driver al werken (je hebt het discovery script al gedraaid), dan ben je al bijna klaar.

---

## Stap 1: Claude Desktop installeren

Als je Claude Desktop nog niet hebt:

1. Ga naar **https://claude.ai/download**
2. Download de Windows versie
3. Installeer en log in met je Anthropic account

Claude Desktop is nodig omdat MCP servers (nog) niet werken via de browser-versie van Claude.

---

## Stap 2: MCP Server bestanden plaatsen

1. Maak een map aan: `C:\Campai\admcms-mcp\`

2. Kopieer de volgende bestanden naar die map:
   - `server.py` — De MCP server zelf
   - `requirements.txt` — Python dependencies
   - `start_server.bat` — Startscript
   - `test_connection.py` — Test script

---

## Stap 3: Python dependencies installeren

Open een Command Prompt en voer uit:

```
cd C:\Campai\admcms-mcp
pip install -r requirements.txt
```

Dit installeert:
- `mcp[cli]` — Het MCP protocol framework
- `pyodbc` — SQL Server driver (heb je waarschijnlijk al)

Controleer of het gelukt is:

```
python -c "from mcp.server.fastmcp import FastMCP; print('MCP OK')"
```

Je zou `MCP OK` moeten zien.

---

## Stap 4: Database credentials instellen

Open `start_server.bat` in een teksteditor en vul je credentials in:

```batch
set ADMCMS_SERVER=cmsnl.database.windows.net
set ADMCMS_DATABASE=ADMCMS-PRODUCTION-REPL01
set ADMCMS_USERNAME=jouw-username
set ADMCMS_PASSWORD=jouw-wachtwoord
set ADMCMS_DRIVER=ODBC Driver 18 for SQL Server
```

Dit zijn dezelfde credentials als je eerder gebruikte voor het discovery script.

---

## Stap 5: Verbinding testen

Voordat je Claude koppelt, test eerst of alles werkt:

```
cd C:\Campai\admcms-mcp
set ADMCMS_USERNAME=jouw-username
set ADMCMS_PASSWORD=jouw-wachtwoord
python test_connection.py
```

Je zou moeten zien:

```
  ADMCMS MCP Server — Connection Test
============================================================

🔍 Test 1: pyodbc installed...
   ✅ pyodbc 5.x.x
🔍 Test 2: mcp package installed...
   ✅ mcp package found
🔍 Test 3: ODBC driver...
   ✅ Driver found
🔍 Test 4: Credentials configured...
   ✅ Username: jouw-username
🔍 Test 5: Connecting...
   ✅ Connected!
🔍 Test 6: Running test query...
   ✅ Salesinvoice table has 1,610,863 rows
🔍 Test 7: Testing date query pattern...
   ✅ Date filtering works correctly
🔍 Test 8: Testing join pattern...
   ✅ Joins work correctly

============================================================
  ✅ ALL TESTS PASSED — MCP server is ready to use!
============================================================
```

Als er fouten zijn, los die eerst op voordat je doorgaat.

---

## Stap 6: Claude Desktop configureren

Nu moet je Claude Desktop vertellen waar de MCP server staat.

### 6a. Config bestand openen

1. Open Windows Verkenner
2. Typ in de adresbalk: `%APPDATA%\Claude`
3. Zoek het bestand `claude_desktop_config.json`
4. Als het niet bestaat, maak het aan

### 6b. Config bewerken

Open `claude_desktop_config.json` in een teksteditor en vervang de inhoud met:

```json
{
  "mcpServers": {
    "admcms": {
      "command": "python",
      "args": ["C:\\Campai\\admcms-mcp\\server.py"],
      "env": {
        "ADMCMS_SERVER": "cmsnl.database.windows.net",
        "ADMCMS_DATABASE": "ADMCMS-PRODUCTION-REPL01",
        "ADMCMS_USERNAME": "jouw-username",
        "ADMCMS_PASSWORD": "jouw-wachtwoord",
        "ADMCMS_DRIVER": "ODBC Driver 18 for SQL Server"
      }
    }
  }
}
```

**Let op:**
- Gebruik dubbele backslashes (`\\`) in het pad
- Vul je echte credentials in
- Als je al andere MCP servers hebt geconfigureerd, voeg `"admcms"` toe aan het bestaande `mcpServers` object

### 6c. Claude Desktop herstarten

1. Sluit Claude Desktop volledig af (ook uit de system tray)
2. Start Claude Desktop opnieuw op

### 6d. Controleren of de MCP server actief is

1. Open een nieuw gesprek in Claude Desktop
2. Klik op het hamertje-icoon (🔨) linksonder bij het invoerveld
3. Je zou nu **"ADMCMS Database"** moeten zien met 3 tools:
   - `query_database`
   - `get_schema`
   - `get_lookups`

---

## Stap 7: Gebruiken!

Je kunt nu vragen stellen in Claude Desktop. Voorbeelden:

### Omzet & verkoop
- *"Wat is de totale omzet van 2025?"*
- *"Vergelijk de omzet van Q1 2025 met Q1 2026"*
- *"Welke 10 klanten hebben de hoogste omzet dit jaar?"*
- *"Hoeveel creditnota's zijn er deze maand?"*
- *"Wat is de gemiddelde orderwaarde per betaalmethode?"*

### Producten & voorraad
- *"Welke 20 producten verkopen het beste dit kwartaal?"*
- *"Hoeveel producten hebben een voorraad onder het minimum?"*
- *"Wat is de totale voorraadwaarde per warehouse?"*
- *"Welke producten zijn gemarkeerd als HotItem?"*
- *"Toon alle KIT-producten met hun onderdelen"*

### Klanten
- *"Hoeveel actieve klanten zijn er per land?"*
- *"Welke klanten staan on-hold?"*
- *"Toon de orderhistorie van klant 12345"*
- *"Hoeveel klanten hebben betalingsproblemen?"*

### Inkoop
- *"Hoeveel open inkooporders zijn er?"*
- *"Wat is de top 10 leveranciers op inkoopvolume dit jaar?"*
- *"Welke leveranciersfacturen zijn er deze maand verwerkt?"*

### Warehouse operaties
- *"Hoeveel stock transactions zijn er vandaag?"*
- *"Welke medewerkers hebben de meeste picks gedaan deze week?"*
- *"Hoeveel damaged items zijn er in de bulkbatch checks?"*

### Betalingen
- *"Hoeveel iDeal betalingen zijn er deze maand?"*
- *"Wat is het faalpercentage van betalingen per methode?"*
- *"Hoeveel facturen zijn niet volledig betaald?"*

---

## Hoe het werkt (onder de motorkap)

Wanneer je een vraag stelt:

1. **Claude leest de vraag** en de volledige schema-documentatie die ingebouwd is in de MCP server
2. **Claude genereert een SQL query** die rekening houdt met:
   - `char(8)` datumformaat (`YYYYMMDD`)
   - Impliciete foreign keys (geen formele FK's in de database)
   - De juiste lookup-waarden (warehouse codes, betaalmethodes, etc.)
3. **Claude voert de query uit** via de `query_database` tool
4. **Claude interpreteert de resultaten** en geeft een leesbaar antwoord

De server staat **alleen SELECT queries** toe — er kan niets gewijzigd worden in de database.

---

## Beveiligingsoverwegingen

- De MCP server draait **lokaal op je PC** — er gaat geen data naar externe servers (behalve naar Azure SQL)
- De database is een **readonly replica** — er kan niets gewijzigd worden
- Alleen **SELECT queries** worden geaccepteerd — INSERT/UPDATE/DELETE/DROP worden geblokkeerd
- Er is een **maximum van 500 rijen** per query resultaat
- **Credentials staan lokaal** in de config — deel dit bestand niet

---

## Troubleshooting

### "MCP server not found" in Claude Desktop

- Controleer of het pad in `claude_desktop_config.json` klopt (dubbele backslashes!)
- Controleer of `python` in je PATH staat: open CMD en typ `python --version`
- Herstart Claude Desktop volledig (ook uit system tray)

### "Connection failed" bij het draaien

- Check of je IP nog in de Azure firewall staat
- Check of de credentials kloppen
- Test met: `python test_connection.py`

### Queries geven geen resultaten

- Check of je datumformaat klopt: `'20260101'` niet `'2026-01-01'`
- Check of je de juiste tabel/kolomnamen gebruikt
- Vraag Claude: *"Toon de schema van tabel X"* om de structuur te bekijken

### Langzame queries

- Grote tabellen (StockTransactions 18M, SalesinvoiceLines 12.6M) zijn traag zonder filters
- Voeg altijd een datumfilter toe
- Gebruik `TOP` om het aantal rijen te beperken
- Zeg tegen Claude: *"Beperk tot de laatste 3 maanden"*

### MCP server crashed

- Open CMD en draai handmatig: `python C:\Campai\admcms-mcp\server.py`
- Kijk naar de foutmelding
- Meest voorkomend: netwerk timeout naar Azure SQL

---

## Bestandsoverzicht

```
C:\Campai\admcms-mcp\
├── server.py              ← De MCP server (het brein)
├── requirements.txt       ← Python dependencies
├── start_server.bat       ← Handmatig starten (voor testen)
├── test_connection.py     ← Verbindingstest

%APPDATA%\Claude\
└── claude_desktop_config.json  ← Claude Desktop MCP configuratie
```

---

## Volgende stappen

Na succesvolle installatie kun je overwegen:

1. **Standaard-vragen documenteren** — Maak een lijst van de meest gebruikte vragen voor de klant
2. **Dashboard bouwen** — Gebruik de MCP server als backend voor een interactief HTML dashboard
3. **Automatische rapportage** — Combineer met de Rewst report templates voor geautomatiseerde klantrapportages
4. **Extra MCP tools** — Voeg tools toe voor specifieke use cases (bijv. een `generate_report` tool die een compleet maandrapport genereert)
