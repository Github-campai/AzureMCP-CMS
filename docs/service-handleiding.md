# ADMCMS MCP Server — Service Handleiding

Praktische handleiding voor het draaien, testen en koppelen van de ADMCMS MCP server. Lezen op volgorde voor eerste setup; daarna secties los bruikbaar als naslag.

**Laatst bijgewerkt:** 2026-04-18
Wijzigingen worden bijgehouden in [§11 Wijzigingslogboek](#11-wijzigingslogboek) onderaan.

---

## Inhoud

1. [Wat is deze service?](#1-wat-is-deze-service)
2. [Eenmalige setup](#2-eenmalige-setup)
3. [Service starten (lokaal dev)](#3-service-starten-lokaal-dev)
4. [Service stoppen](#4-service-stoppen)
5. [Poortconfiguratie](#5-poortconfiguratie)
6. [Testen](#6-testen)
7. [Remote connecties (Claude Desktop / Claude Code)](#7-remote-connecties-claude-desktop--claude-code)
8. [Nieuwe tools toevoegen](#8-nieuwe-tools-toevoegen)
9. [Troubleshooting](#9-troubleshooting)
10. [Bestandenoverzicht](#10-bestandenoverzicht)
11. [Wijzigingslogboek](#11-wijzigingslogboek)

---

## 1. Wat is deze service?

HTTP-based MCP (Model Context Protocol) server die Claude en andere AI agents toegang geeft tot de ADMCMS Azure SQL replica. De server draait als Python ASGI applicatie met Starlette + FastMCP, valideert Entra JWT tokens (in productie) en biedt 6 read-only tools:

- `query_database` — free-form SELECT
- `get_schema` — tabelkolommen
- `get_lookups` — unieke waarden per kolom
- `search_products` — producten zoeken
- `get_revenue` — omzet met grouping
- `get_customer_orders` — orders per klant

De service-modus is alleen **HTTP** (geen stdio meer). Voor ontwikkeling lokaal, straks remote via Azure.

**Architectuur in 1 zin:** request → EntraAuthMiddleware → /mcp (Streamable HTTP) → tool handler → pyodbc → Azure SQL replica.

---

## 2. Eenmalige setup

### Vereisten

- **Windows 10/11** (dit document is voor Windows; Linux/Mac werken ook maar commands verschillen)
- **Python 3.12+** — check met `python --version`
- **ODBC Driver 18 for SQL Server** — [download van Microsoft](https://learn.microsoft.com/nl-nl/sql/connect/odbc/download-odbc-driver-for-sql-server)
- **Git** (voor code-updates uit GitHub)
- **Netwerk:** jouw huidige publieke IP moet op de ADMCMS SQL firewall allowlist staan (via CMS Lelystad)

### Eerste installatie

In PowerShell, navigeer naar de project folder:

```powershell
cd "c:\Users\roland\OneDrive - Campai B.V\Claud Projects\CMS MCP Server\cms-mcp"
```

Maak een virtual environment en installeer dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Als `Activate.ps1` een error over Execution Policy geeft, eenmalig:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Check installatie:

```powershell
pip list | Select-String admcms-mcp
# Moet tonen: admcms-mcp 0.1.0 (editable install)
```

---

## 3. Service starten (lokaal dev)

### PowerShell

Eén script voor alle env-vars + start. Plak in PowerShell (project folder + venv actief):

```powershell
$env:AUTH_DISABLED = "true"
$env:KEY_VAULT_URI = "https://dummy.vault.azure.net/"
$env:ENTRA_APP_ID = "dummy"
$env:ENTRA_ALLOWED_TENANTS = "dummy"
$env:ADMCMS_DB_USERNAME = "saazuredb"
$env:ADMCMS_DB_PASSWORD = "<zie Key Vault: cms-mcp-kv-prod / admcms-db-password>"
uvicorn admcms_mcp.server:app --port 8000
```

**Uitleg per variabele:**

| Variabele | Doel |
|-----------|------|
| `AUTH_DISABLED=true` | Schakelt JWT-validatie uit voor lokale ontwikkeling. **Nooit true in productie.** |
| `KEY_VAULT_URI` | Azure Key Vault URL. Met `AUTH_DISABLED=true` niet gebruikt, maar vereist als env var. |
| `ENTRA_APP_ID` | Application ID van de Entra App Registration. Idem, dummy in dev. |
| `ENTRA_ALLOWED_TENANTS` | Lijst van tenant-IDs die mogen authenticeren. Idem, dummy in dev. |
| `ADMCMS_DB_USERNAME` | SQL gebruikersnaam (leveranciersgegeven, niet wijzigen). |
| `ADMCMS_DB_PASSWORD` | SQL wachtwoord (leveranciersgegeven, niet wijzigen). |

### Git Bash (alternatief)

Zelfde env-vars maar Unix-syntax:

```bash
source .venv/Scripts/activate
AUTH_DISABLED=true \
KEY_VAULT_URI=https://dummy.vault.azure.net/ \
ENTRA_APP_ID=dummy \
ENTRA_ALLOWED_TENANTS=dummy \
ADMCMS_DB_USERNAME=saazuredb \
ADMCMS_DB_PASSWORD=<zie-key-vault> \
uvicorn admcms_mcp.server:app --port 8000
```

### Bevestiging dat de service draait

Je moet deze regels zien:

```
AUTH_DISABLED is true — all requests bypass JWT validation. This must never be enabled in production.
INFO:     Started server process [XXXXX]
INFO:     Waiting for application startup.
INFO:     StreamableHTTP session manager started
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

Snelle check in een tweede venster:

```powershell
Invoke-RestMethod http://localhost:8000/healthz
# Verwacht: status = ok
```

### Optioneel: auto-reload tijdens development

Voeg `--reload` toe om bij code-wijzigingen automatisch te herstarten:

```powershell
uvicorn admcms_mcp.server:app --port 8000 --reload
```

**Let op:** auto-reload werkt niet altijd betrouwbaar bij wijzigingen in modules met lifespan/task groups. Bij onverklaarbaar gedrag: handmatig herstarten (Ctrl+C en opnieuw).

---

## 4. Service stoppen

### Tijdens dev (in terminal)

In het terminal-venster waar uvicorn draait: **Ctrl+C**.

```
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     StreamableHTTP session manager shutting down
INFO:     Application shutdown complete.
INFO:     Finished server process [XXXXX]
```

### Als het terminal-venster gesloten is

Het proces draait mogelijk nog. Check en kill:

```powershell
Get-Process | Where-Object { $_.ProcessName -eq "python" -and $_.MainWindowTitle -like "*uvicorn*" }
# OF simpelweg alle Python-processen checken:
Get-Process python
# Kill op basis van PID:
Stop-Process -Id <PID> -Force
```

Port checken of nog in gebruik:

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
```

---

## 5. Poortconfiguratie

### Standaard poort

`8000` — hardcoded in alle documenten en test-scripts.

### Poort wijzigen

**Optie A — eenmalig bij starten:**

```powershell
uvicorn admcms_mcp.server:app --port 9000
```

Daarna client-scripts aanpassen (`scripts/test_mcp_client.py`, `scripts/smoke_test_new_tools.py`): geef URL als argument mee:

```powershell
python scripts/smoke_test_new_tools.py http://localhost:9000/mcp
```

**Optie B — standaard poort veranderen:**

Wijzig in onderstaande files:
- [docs/service-handleiding.md](service-handleiding.md) — deze file
- [scripts/test_mcp_client.py:146](../scripts/test_mcp_client.py#L146) — default URL
- [scripts/smoke_test_new_tools.py:87](../scripts/smoke_test_new_tools.py#L87) — default URL
- [scripts/discover_schemas.py:32](../scripts/discover_schemas.py#L32) — default URL
- [Dockerfile:17](../Dockerfile#L17) — `ENV PORT=8000` en `EXPOSE 8000`

**Optie C — via environment variable:**

```powershell
$env:PORT = "9000"
uvicorn admcms_mcp.server:app --port $env:PORT
```

In Azure App Service wordt PORT automatisch geïnjecteerd; zie Dockerfile CMD.

---

## 6. Testen

### Unit tests (snel, geen DB nodig)

In project-root met venv actief:

```powershell
pytest -v
# Verwacht: 96 tests passing in ~2 seconden
```

Specifieke test class/file:

```powershell
pytest tests/test_tools.py::TestGetRevenue -v
```

Met coverage rapport:

```powershell
pytest --cov=admcms_mcp --cov-report=term-missing
```

### Lint & format check

```powershell
ruff check src tests
ruff format --check src tests
# Auto-fix:
ruff check --fix src tests
ruff format src tests
```

### Live smoke test (vereist draaiende service + DB-toegang)

Twee vensters nodig: venster 1 met uvicorn draaiend (zie §3), venster 2:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts/smoke_test_new_tools.py
```

Test alle 6 tools met realistische parameters en toont ✅/❌ per call.

### Schema-ontdekking (voor nieuwe tools)

Als je SQL wil schrijven met onbekende kolom-namen:

```powershell
python scripts/discover_schemas.py > schemas.txt
```

Dumpt kolommen van SALESINVOICE, SALESINVOICELINES, SalesOrderHeader. Pas `TABLES` in het script aan voor andere tabellen.

### Interactieve REPL tegen de live DB

```powershell
python scripts/test_mcp_client.py --repl
```

Voorbeelden:

```
admcms> SELECT TOP 10 * FROM PRODUCT
admcms> schema SALESINVOICE
admcms> lookups PRODUCT Make
admcms> quit
```

---

## 7. Remote connecties (Claude Desktop / Claude Code)

### Claude Desktop (met remote MCP HTTP)

1. Open **Claude Desktop → Settings → MCP Servers** (of locatie `%APPDATA%\Claude\claude_desktop_config.json`).
2. Voeg toe / wijzig:

```json
{
  "mcpServers": {
    "admcms-local": {
      "url": "http://localhost:8000/mcp",
      "transport": "http"
    }
  }
}
```

3. Start de uvicorn service (zie §3).
4. Start/herstart Claude Desktop. Open een nieuwe chat en vraag bijvoorbeeld: *"Wat was de omzet afgelopen maand?"*
5. Claude Desktop ziet de 6 tools en gebruikt `get_revenue` automatisch.

**Belangrijk:** bij `AUTH_DISABLED=true` is er geen SSO login nodig — handig voor dev. Voor productie komt Entra SSO erbij (zie design spec).

### Claude Code CLI

Koppel de MCP server aan je Claude Code sessie:

```powershell
claude mcp add admcms-local --transport http http://localhost:8000/mcp
```

Herstart Claude Code (`/exit`, opnieuw starten). Je kunt nu in de chat vragen stellen; Claude gebruikt de tools.

Verwijderen als je klaar bent:

```powershell
claude mcp remove admcms-local
```

### Browser-frontend ([web/chat.html](../web/chat.html))

Simpele HTML chat-interface die de Anthropic API rechtstreeks aanroept met MCP connector support (`anthropic-beta: mcp-client-2025-11-20`). **Belangrijke flow-details:**

1. Browser → `api.anthropic.com` (met `anthropic-dangerous-direct-browser-access: true`)
2. Anthropic's cloud → jouw MCP server URL

Omdat **Anthropic** (niet de browser) jouw MCP server aanroept, werkt `localhost:8000` NIET. De URL moet publiek bereikbaar zijn via HTTPS. Voor lokale dev gebruiken we cloudflared om een tijdelijke tunnel te maken.

#### Volledige procedure — stap voor stap (eenmalig + herhaald)

**Eenmalig (eerste keer):**

1. **Download cloudflared** (1 bestand, geen install nodig):
   ```powershell
   Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile "cloudflared.exe"
   ```
   (staat in `.gitignore`, dus veilig in project-root)

2. **Maak een Anthropic API key** met beperkte scope op [console.anthropic.com](https://console.anthropic.com).

**Per test-sessie (3 vensters, in volgorde):**

**Venster 1 — MCP server:**
```powershell
.\.venv\Scripts\Activate.ps1
$env:AUTH_DISABLED = "true"
$env:KEY_VAULT_URI = "https://dummy.vault.azure.net/"
$env:ENTRA_APP_ID = "dummy"
$env:ENTRA_ALLOWED_TENANTS = "dummy"
$env:ADMCMS_DB_USERNAME = "saazuredb"
$env:ADMCMS_DB_PASSWORD = "<zie Key Vault: cms-mcp-kv-prod / admcms-db-password>"
uvicorn admcms_mcp.server:app --port 8000
```
Wacht op `StreamableHTTP session manager started` + `Application startup complete`.

**Venster 2 — cloudflared tunnel:**
```powershell
.\cloudflared.exe tunnel --url http://localhost:8000
```
Output bevat een regel zoals:
```
https://adjective-noun-adjective-noun.trycloudflare.com
```
**Kopieer die URL.** Bij elke nieuwe `cloudflared` start krijg je een ándere URL — dus bijwerken in de browser-config elke sessie.

**Venster 3 — browser (alleen eerste keer per URL):**
1. Open `web/chat.html` (dubbelklik op de file, opent in default browser)
2. Klik **Configuratie** (rechtsboven of linksbeneden in sidebar)
3. Vul in:

   | Veld | Waarde |
   |------|--------|
   | Anthropic API Key | jouw `sk-ant-...` key |
   | Model | Claude Sonnet 4.5 (default) |
   | MCP Server URL | `https://...trycloudflare.com/mcp/` **(let op trailing `/`)** |
   | MCP Authorization Token | *leeg* (want AUTH_DISABLED=true) |
   | System prompt | default is prima |

4. **Opslaan** — linksonder zie je groene dot + "Geconfigureerd"
5. Typ een vraag (of klik een suggestion) → Enter

**Verificatie (optioneel):**
```powershell
# In apart venster voor health check:
Invoke-RestMethod http://localhost:8000/healthz
# Verwacht: status = ok
```

#### Afsluiten

- Ctrl+C in venster 2 (cloudflared) — sluit de publieke tunnel direct
- Ctrl+C in venster 1 (uvicorn) — stopt de MCP server

#### Troubleshooting

| Error in browser | Oorzaak | Oplossing |
|------------------|---------|-----------|
| `Fout: tools.0.mcp_toolset.mcp_server_name: Field required` | oude HTML gecached | Ctrl+F5 voor hard reload |
| `Fout: MCP server 'X' is defined but not referenced by any mcp_toolset` | zelfde cache-probleem | Ctrl+F5 |
| `HTTP 502 Bad Gateway` | uvicorn draait niet | Check venster 1 — opnieuw starten |
| `HTTP 421 Misdirected Request` | MCP DNS-rebinding protection actief | Al gefixt in code (`TransportSecuritySettings(enable_dns_rebinding_protection=False)`). Pull laatste code + herstart uvicorn |
| `Connection error while communicating with MCP server` | geen internet, of tunnel URL klopt niet | Check tunnel nog draait (venster 2), check URL in config incl `/mcp/` trailing slash |
| "Geconfigureerd" blijft grijs | veld leeg | API key én MCP URL beide vereist |

**⚠️ Beveiligingscaveat:** met `AUTH_DISABLED=true` is de tunnel-URL een **open** DB-query endpoint. Alleen voor korte tests (10-30 min) gebruiken en tunnel direct sluiten wanneer klaar. Productie loopt via Azure App Service met Entra JWT auth (Phase 3).

#### Alternatief: ngrok

```powershell
Invoke-WebRequest -Uri "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip" -OutFile "ngrok.zip"
Expand-Archive ngrok.zip -DestinationPath .
.\ngrok.exe http 8000
```
Werkt hetzelfde; URL in de ngrok UI (`http://127.0.0.1:4040`) of in output.

### Legacy: stdio-mode (OUD, niet meer gebruiken)

De originele setup via `C:\Campai\admcms-mcp\server.py` met `"command": "python"` in claude_desktop_config.json is **verouderd**. Dat bestand kan gearchiveerd worden zodra je overschakelt naar de HTTP variant.

---

## 8. Nieuwe tools toevoegen

Kort stappenplan — volg het patroon van bestaande tools.

1. **Business logic** in [src/admcms_mcp/tools.py](../src/admcms_mcp/tools.py) — functie die SQL bouwt via `execute_query_params`, retourneert `format_response(data=..., summary=..., error=...)`.
2. **Tests** in [tests/test_tools.py](../tests/test_tools.py) — mock `execute_query_params`, test happy/error paden.
3. **Register** in [src/admcms_mcp/server.py](../src/admcms_mcp/server.py) — `@mcp.tool()` decorator met goede docstring (die leest Claude).
4. **Smoke test** lokaal: uvicorn herstarten, `python scripts/smoke_test_new_tools.py`.

**Kolom-namen die onzeker zijn:** eerst `python scripts/discover_schemas.py` draaien om te verifiëren. Nooit gokken — dat produceert bugs die pas bij gebruik opvallen.

**Helpers in [src/admcms_mcp/query_helpers.py](../src/admcms_mcp/query_helpers.py):**
- `admcms_date("2026-04-01")` → `"20260401"` (ISO → char(8))
- `sanitize_like(term)` → escape LIKE metacharacters
- `format_response(data, summary, error)` → consistent output shape

---

## 9. Troubleshooting

### "All connection attempts failed" bij smoke test

De uvicorn service draait niet. Check venster 1 — server moet `Uvicorn running on http://127.0.0.1:8000` tonen. Zo niet: opnieuw starten (zie §3).

### "source : The term 'source' is not recognized"

Je gebruikt PowerShell maar typte Unix-syntax. Gebruik `.\.venv\Scripts\Activate.ps1` ipv `source .venv/Scripts/activate`.

### "AUTH_DISABLED=true : The term 'AUTH_DISABLED=true' is not recognized"

Idem — PowerShell gebruikt `$env:VAR = "value"` niet `VAR=value`. Zie §3 PowerShell-versie.

### `Invoke-RestMethod : Internal Server Error` op /mcp

500 = crash in MCP-handler. Check venster 1 voor Python stack trace. Meestal een kolom-naam fout in SQL → run `discover_schemas.py` om correcte namen te vinden.

### `Invalid column name 'X'` in tool-output

Kolom bestaat niet in ADMCMS. Dit gebeurt als je gokt ipv verifieert. Oplossing: `python scripts/discover_schemas.py`, tools.py fixen, uvicorn **handmatig herstarten** (niet vertrouwen op --reload bij dit type wijziging).

### Tests falen na pull van nieuwe commits

Waarschijnlijk nieuwe dependencies. Draai:

```powershell
pip install -e ".[dev]"
```

### "port 8000 is already in use"

Vorige uvicorn draait nog. Kill of gebruik andere poort (§5).

### Claude Desktop ziet de tools niet

- Check dat uvicorn draait en `/healthz` antwoord geeft
- Check `claude_desktop_config.json` syntax (valid JSON, transport="http")
- Herstart Claude Desktop volledig (niet alleen nieuwe chat)

---

## 10. Bestandenoverzicht

```
cms-mcp/
├── src/admcms_mcp/
│   ├── server.py          # Starlette app + MCP mount + Entra auth middleware
│   ├── tools.py           # 6 tool-functies (business logic)
│   ├── query_helpers.py   # datum-conversie, LIKE escape, response formatter
│   ├── db.py              # connection, validate_query, execute_query, execute_query_params
│   ├── auth.py            # JWT validatie + allowlist check
│   ├── config.py          # env-var settings (pydantic)
│   ├── keyvault.py        # Azure Key Vault secret loader (met env-override voor dev)
│   └── schema.py          # SCHEMA_KNOWLEDGE string (ADMCMS tabelbeschrijvingen)
├── tests/                 # 96 unit tests (mocked DB)
├── scripts/
│   ├── test_mcp_client.py        # interactieve REPL
│   ├── smoke_test_new_tools.py   # smoke test van alle 6 tools
│   └── discover_schemas.py       # dump kolomnamen voor bekende tabellen
├── docs/
│   ├── service-handleiding.md    # dit document
│   ├── handleiding.md            # legacy stdio-mode handleiding (archief)
│   ├── DATABASE-KENNIS/          # ADMCMS datamodel + query cookbook
│   └── superpowers/              # design specs + implementatie plannen
├── infra/                        # (toekomstig) Bicep voor Azure deployment
├── Dockerfile                    # Python 3.12 + ODBC Driver 18 image
├── pyproject.toml                # deps + lint/test config
├── .env.example                  # template voor lokale env vars
└── README.md                     # korte project-intro
```

---

## 11. Wijzigingslogboek

Noteer hier iedere relevante service-wijziging in omgekeerd-chronologische volgorde (nieuwste bovenaan).

### 2026-04-19 — Admin page + audit log (local SQLite, Azure Table stub)

Elke tool-call wordt nu gelogd met args, alle uitgevoerde SQL queries,
duration, user, error en result summary. Opslag via pluggable store:
- **Default** `AUDIT_STORE=local` → SQLite op `data/audit.db` (override
  via `AUDIT_DB_PATH`). Single-file, thread-safe, zero-dependency.
- `AUDIT_STORE=none` → NullStore (voor tests / audit disabled).
- `AUDIT_STORE=azure_table` → stub; te implementeren bij migratie naar
  shared Azure Storage account.

Nieuwe HTTP endpoints (achter dezelfde Entra auth):
- `GET /admin/tools` — alle geregistreerde MCP tools met input schema
- `GET /admin/audit?tool=&limit=&since=&errors_only=` — audit rijen
- `GET /admin/audit/{id}` — volledige entry incl. elke SQL query
- `GET /admin/stats?since=` — per-tool calls, avg duration, errors (7d)
- `GET /admin/health` — audit store status + uptime

Browser UI in **[web/admin.html](../web/admin.html)** met 4 tabs
(Stats/Audit/Tools/Health). Open direct via file:// en het UI probeert
`http://localhost:8000`; knop met ⚙ in topbar laat je de backend URL
overriden (sessionStorage). CORS toegevoegd in de server zodat de
admin page zonder proxy kan werken.

Alle 20 MCP tools (incl. `query_database`) nu gewrapt met
`_audited()`-helper die row_count + summary + error per call vastlegt.
20 nieuwe tests (12 audit + 8 admin), totaal 185 passing.

### 2026-04-19 — 5 nieuwe analytics tools (batch 2)

Vijf tools die met 100% zekerheid gebouwd konden worden — alle gebruikte
kolommen eerder verified via discover_schemas of live queries:

- **`get_credit_note_analysis`** (InvoiceType='1') — aantallen + totals per
  segment/country/customer/make/month, detecteert positieve credit-facturen
  als anomalie (via anomaly_pct in summary).
- **`get_payment_method_stats`** — PaymentCondition-mix (PREP/AC/CC/etc.),
  inclusief avg_payment_days en pct_fullypaid. Cashflow-indicator.
- **`get_salesrep_performance`** — per Salesrep: #facturen, unieke klanten,
  omzet, AOV. Scope: paid regular invoices (financial-reporting convention).
- **`get_discount_analysis`** — DiscountAmount per segment/customer/make/
  productgroup/salesrep. Gross pre-discount = net + discount. Levert
  discount_pct per row.
- **`get_order_origin_mix`** — OrderOrigin breakdown (web/EDI/phone/etc.),
  aantal lines + invoices + totaal met pct_of_total_lines en YoY.

Alle tools ondersteunen `include_ly` waar zinvol. 8 nieuwe tests,
totaal nu 165 passing. Tools-lijst 15 → 20.

### 2026-04-19 — Shipping cost analysis tool + financial scope filters

- **Nieuwe tool `get_shipping_cost_analysis(start_date, end_date, group_by,
  include_ly, exclude_credits, paid_only, segments, carriers)`**.
  Berekent per groep: order_count, total_charged, avg_charged,
  total_carrier_cost, avg_carrier_cost, shipping_margin, avg_shipping_margin,
  ship_cost_pct_of_turnover. Met `include_ly=True` → YoY delta + delta_pct
  op carrier cost per row.
- `group_by`: `total`, `segment` (B2B/B2C), `carrier` (DHL/FedEx/UPS via
  ShipmentMethod prefix), `shipment_method` (exacte codes FEDE/FEDP/UPSS/etc.),
  `segment_carrier` (default, cross-tab).
- Optional filters: `segments=["B2B"]` en/of `carriers=["FedEx"]`.
- **`get_shipping_summary` uitgebreid** met dezelfde `exclude_credits=True`
  en `paid_only=True` defaults — matcht financial-reporting conventie.
  Flip uit voor breder operationeel zicht.
- **Context:** Analyse van Mrt 2026 vs Mrt 2025 met de klant liet zien
  dat carrier-kosten flink gestegen zijn (FedEx +31% B2C) maar de
  doorbelasting aan klanten niet meegroeit → shipping-marge B2C bijna
  verdampt (€2,27 → €0,51 per order, -78%). Deze tool maakt dit soort
  kwartaal-analyses in één call reproduceerbaar.
- 7 nieuwe tests, totaal nu 157 passing.
- Tools-lijst in README van 14 naar 15.

### 2026-04-18 — 3 performance-tools voor PowerBI-replicatie + YoY support

Ter voorbereiding op klant-overleg over het PowerBI YTD dashboard:

- **get_revenue uitgebreid** met `group_by="country"` en `group_by="productgroup"`,
  plus een `include_ly` parameter die dezelfde periode vorig jaar ophaalt en
  `amount_ly / delta / delta_pct` per rij meegeeft. LY-merging houdt ook rekening
  met groepen die alleen in LY of alleen in het huidige jaar voorkomen.
- **get_margin_report** — nieuwe tool voor bruto-marge analyse op line-niveau
  (`SalesinvoiceLines.InvoiceAmountHC - CostAmountBookedHC`). Group_by: total,
  country, make, productgroup, customer, segment. Met `include_ly` voor YoY.
- **get_kpi_summary** — één call voor alle top-KPI-cards van een dashboard:
  turnover, turnover_incl_shipping, invoice_count, ordering_customers, aov,
  shipping_revenue/cost/margin, line_count, item_count. Met `include_ly=True`
  (default) komen er `_ly`, `_delta`, `_delta_pct` varianten bij.
- Nieuwe helper `shift_year()` in query_helpers.py — handelt leap-day correct.
- 17 nieuwe unit tests, totaal nu 150 passing.
- Tools-lijst in README van 12 naar 14.

**Validatie met klant:** draai gelijke dates in PowerBI en via deze tools,
vergelijk cijfers. Verschillen = definitie-mismatch om te onderzoeken.

### 2026-04-18 — Vandaag's datum wordt altijd meegestuurd naar Claude

**Observatie:** Claude interpreteerde "deze maand" als april 2025 (training-data),
terwijl we in april 2026 zitten. Fix in [web/chat.html](../web/chat.html):

- Elke API-call krijgt nu een `CURRENT DATE: YYYY-MM-DD` prefix bovenop de
  system prompt (fresh berekend bij elke send, niet gecachet)
- Default system prompt bevat ook een uitgebreide "Huidige datum" sectie met
  mappings voor "vandaag / deze maand / dit jaar / vorige maand / Q1"
- Werkt direct bij nieuwe chat-sessies; bestaande sessies hoeven system prompt
  niet te refreshen omdat de datum-prefix onafhankelijk van de stored prompt
  werkt

### 2026-04-18 — Paging op alle list-tools + `detect_price_anomalies`

- Nieuwe helpers `paging_clause()` en `paging_summary()` in
  [query_helpers.py](../src/admcms_mcp/query_helpers.py) — uniforme OFFSET/FETCH + summary shape.
- 4 tools kregen `offset: int = 0` parameter:
  `search_products`, `get_stock_levels`, `get_customer_orders`, `detect_price_anomalies`.
- Response-summary bevat nu `offset`, `limit`, `returned`, `has_more`, `next_offset`
  zodat Claude automatisch door kan paginëren.
- `has_more` heuristiek: `returned >= limit`. Soms false-positive bij exacte match,
  dan krijgt next page 0 rijen. Simpel en betrouwbaar.
- Nieuwe tool `detect_price_anomalies(start_date, end_date, min_price=10000, limit=50, offset=0)` —
  data-quality check voor onverwachte prijzen (bijvoorbeeld barcode scans in
  price-velden). Verplicht datum-bereik voorkomt full-scan timeout.
- 9 nieuwe tests (paging helpers + paging integratie), totaal nu 133 passing.

### 2026-04-18 — Claude-guidance verbeterd na observatie verkeerde kolomnamen

Logs toonden dat Claude bij complexe vragen `query_database` koos met gegokte
kolomnamen (CustSuppnr op Salesinvoice i.p.v. Customer; InvoiceTotal i.p.v. LineTotal;
Linenr i.p.v. Orderline) — resulterend in SQL-fouten.

Twee mitigaties:

1. **Default system prompt in [web/chat.html](../web/chat.html)** herschreven met:
   - Expliciete tool-strategie: kies ALTIJD eerst een dedicated tool boven query_database
   - Lijst van veelvoorkomende vergissingen (CustSuppnr, InvoiceTotal, Linenr, Company, etc.)
   - Regel: vóór elke query_database-call eerst get_schema draaien op betrokken tabellen

2. **SCHEMA_KNOWLEDGE in [src/admcms_mcp/schema.py](../src/admcms_mcp/schema.py)** uitgebreid met
   een "COMMON COLUMN-NAME MISTAKES" sectie direct onder de foreign-key sectie. Claude leest
   dit bij elke init via de MCP resource `admcms://schema-knowledge`.

Bestaande chat-sessies moeten op Configuratie → System prompt klikken en de default
handmatig resetten (leeg maken en opslaan) om de nieuwe prompt te krijgen.

### 2026-04-18 — Twee tools toegevoegd: `get_stock_levels` + `get_supplier_purchases`

- `get_stock_levels(product_code, warehouse, only_available, limit)` — voorraad uit
  ProductWarehouse met composable filters (product, warehouse, available-only)
- `get_supplier_purchases(start_date, end_date, limit, include_corrupt)` — top-N
  leveranciers op PurchaseOrderInvoice.GoodsAmount. Sluit supplier 81 (VDM PART,
  data-corruptie) standaard uit
- Schema verificatie via `scripts/discover_schemas.py` uitgebreid met ProductWarehouse,
  PurchaseOrder, PurchaseOrderInvoice, PurchaseOrderInvoices
- 9 nieuwe unit tests, totaal nu 118 passing
- Tools-lijst in README van 9 naar 11

### 2026-04-18 — Twee tools toegevoegd: `get_top_customers` + `get_shipping_summary`

- `get_top_customers(start_date, end_date, limit=10)` — top-N klanten op omzet in
  een periode, filtert op CustSuppType='1' (alleen klanten, geen leveranciers)
- `get_shipping_summary(start_date, end_date)` — shipping metrics: total_ship_cost
  (gefactureerd), total_ship_cost_calculated (systeem-baseline), delta (= kortingen/
  overrides), total_order_costs, invoice_count
- 8 nieuwe unit tests, totaal nu 109 passing
- Tools-lijst in README van 7 naar 9

### 2026-04-18 — Tool toegevoegd: `get_revenue_by_business_unit`

- Nieuwe tool die omzet per business-unit classificatie retourneert (B2C/B2B/IC/Overig)
  op basis van CustSupplier.CustSuppGroup mapping
- Vastgezet als dedicated tool zodat Claude niet meer 3 aparte calls (get_schema,
  get_lookups, query_database) nodig heeft voor deze veelvoorkomende vraag
- 5 nieuwe unit tests toegevoegd, totaal nu 101 passing
- Bestanden: [src/admcms_mcp/tools.py](../src/admcms_mcp/tools.py),
  [src/admcms_mcp/server.py](../src/admcms_mcp/server.py),
  [tests/test_tools.py](../tests/test_tools.py)

### 2026-04-18 — Browser-frontend werkt end-to-end + uitgebreide procedure

- **§7 bijgewerkt** met volledige 3-venster stap-voor-stap procedure (download cloudflared → start uvicorn → start tunnel → configureer browser)
- Troubleshooting-tabel toegevoegd met de errors die we onderweg tegenkwamen
- HTML fixes (`tools[].mcp_toolset.mcp_server_name` — Anthropic API beta vereist expliciete toolset-referentie)
- Code fix: `TransportSecuritySettings(enable_dns_rebinding_protection=False)` in server.py — MCP SDK's default host-check blokkeert tunnel URLs met HTTP 421
- End-to-end verificatie: browser → Anthropic API → cloudflared tunnel → MCP server → ADMCMS replica = werkend

### 2026-04-18 — Browser-frontend + Cloudflare Tunnel documentatie

- `campai-admcms-chat.html` verplaatst naar `web/chat.html`.
- §7 uitgebreid met cloudflared / ngrok setup voor publieke tunnel-URL.
- De frontend gebruikt Anthropic's `mcp-client-2025-11-20` beta-header; Anthropic's cloud roept de MCP URL aan, dus localhost werkt niet.

### 2026-04-18 — Service handleiding toegevoegd

- Dit document aangemaakt in `docs/service-handleiding.md`.
- Eerste versie: setup, start/stop, poort, tests, remote clients, nieuwe tools, troubleshooting, bestanden.

### 2026-04-18 — 3 dedicated tools + query helpers (PR `feat/tools-mvp`)

- Nieuw: `search_products`, `get_revenue`, `get_customer_orders`.
- Nieuwe module `query_helpers.py` (admcms_date, sanitize_like, format_response + ongebruikte company_filter).
- Nieuw: `execute_query_params` voor parameterized queries.
- Live smoke test bevestigd: alle 6 tools werken tegen ADMCMS replica.
- Column-name corrections na schema-discovery: `TotalAmount→LineTotal`, `ShippingDate→DeliveryDate`, `CustSuppnr→Customer` op SALESINVOICE, etc.
- Scripts toegevoegd: `discover_schemas.py`, `smoke_test_new_tools.py`.

### 2026-04-18 — Remote HTTP MCP server v1 (PR `feat/remote-http-server`)

- Stdio-mode volledig verwijderd; HTTP-only via Starlette + FastMCP Streamable HTTP.
- Entra JWT middleware + hybrid allowlist (security reviewed, header-injection safe, fail-closed).
- Docker-image + .gitignore + README + project refactor naar `src/admcms_mcp/`.
- 60 unit tests, 70% coverage.

### Template voor toekomstige entries

```markdown
### YYYY-MM-DD — Korte titel

- Wijziging 1 (wat is anders)
- Wijziging 2
- Waarom deze wijziging werd gedaan (indien niet voor de hand liggend)
- Bestanden gewijzigd (indien relevant)
```
