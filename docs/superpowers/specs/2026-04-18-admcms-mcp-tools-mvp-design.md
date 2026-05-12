# ADMCMS MCP — Dedicated Tools MVP Design

**Datum:** 2026-04-18
**Auteur:** Roland Slagter (Campai) + Claude
**Status:** Design approved — ready for implementation planning
**Scope:** First iteration of dedicated tools (T1–T3). T4/T5 plus shipping and supplier tooling deferred to a follow-up iteration once the pattern is proven.

---

## 1. Context & Doel

De huidige MCP server (branch `main`, commit `4681f11`) heeft drie generieke tools: `query_database`, `get_schema`, `get_lookups`. Claude Desktop-gebruikers stellen vragen als *"wat was onze omzet in maart?"* en Claude schrijft dan ter plekke SQL en roept `query_database` aan. Dat werkt, maar:

- **Veiligheid** — elke query is ad-hoc; kolom-namen verkeerd raden geeft errors
- **Snelheid** — Claude besteedt tokens aan SQL bouwen in plaats van de vraag beantwoorden
- **Discoverability** — MCP-clients tonen alleen 3 generieke tools; de mogelijkheden blijven verborgen
- **Consistency** — twee vergelijkbare vragen kunnen verschillende SQL oplossingen opleveren

**Doel:** dedicated tools voor de meest voorkomende vraag-patronen, typed parameters, consistente output, getest met mocked DB-calls. MVP-scope: drie tools die samen sales-analyse, open orders en product-catalogus dekken. Bewijs het patroon werkt, daarna iteratief uitbreiden.

## 2. Architecturale Beslissingen

| Onderwerp | Keuze |
|-----------|-------|
| Scope MVP | T1 `search_products`, T2 `get_revenue`, T3 `get_customer_orders` |
| Approach | Thin wrappers (Approach A uit de brainstorm) — geen DSL, geen query-builder |
| Nieuwe module | `src/admcms_mcp/query_helpers.py` — datum-conversie, company-filter, LIKE-escape, output formatter |
| Output contract | `{"data": list, "summary": dict | None, "error": str | None}` |
| Datum-format | Tools accepteren ISO `YYYY-MM-DD`; intern naar ADMCMS `char(8) YYYYMMDD` |
| Company dimensie | Optional param op elke tool; `None` = geen filter (alle bedrijven samen) |
| Error handling | Niet raisen; `{"error": "..."}` return matcht bestaande tools.py-stijl |
| Testing | Mocked `execute_query`; >85% op helpers, >80% op nieuwe tools |
| Schema-verifiëring | Onbekende kolomnamen **niet gokken**; implementer verifieert tegen SCHEMA_KNOWLEDGE en live DB |

## 3. Module Layout

```
src/admcms_mcp/
├── query_helpers.py    # NIEUW
├── tools.py            # bestaande 3 + 3 nieuwe
├── server.py           # +3 @mcp.tool() registraties
└── (rest ongewijzigd)

tests/
├── test_query_helpers.py   # NIEUW
├── test_tools.py           # bestaande + 3 nieuwe TestXxx klassen
└── (rest ongewijzigd)
```

## 4. `query_helpers.py`

### 4.1 `admcms_date(iso: str) -> str`

Converteert ISO datum naar ADMCMS `char(8)`:

```python
admcms_date("2026-04-18")   # -> "20260418"
admcms_date("2026-04-1")    # -> ValueError("Invalid ISO date...")
admcms_date("nonsense")     # -> ValueError("Invalid ISO date...")
```

Accepteert alleen het exacte formaat `YYYY-MM-DD` (10 chars). Geen locale-tolerantie.

### 4.2 `company_filter(company: str | None) -> tuple[str, tuple]`

Retourneert SQL-fragment en parameters voor concatenatie:

```python
company_filter(None)       # -> ("", ())
company_filter("CMS001")   # -> (" AND Company = ?", ("CMS001",))
```

Kolom-naam `Company` is de aanname — implementer verifieert tegen SCHEMA_KNOWLEDGE of live DB. Als de echte kolom anders heet (`Mandant`, `CompanyCode`, etc.), past implementer de helper eenmalig aan — alle tools gebruiken dezelfde helper.

### 4.3 `sanitize_like(term: str) -> str`

Escapet LIKE-metacharacters zodat gebruikers-input niet per ongeluk patronen matcht:

```python
sanitize_like("50%")        # -> "50[%]"
sanitize_like("item_3")     # -> "item[_]3"
sanitize_like("a[b]")       # -> "a[[]b]"
```

T-SQL gebruikt `[...]` character classes om metacharacters te escapen (niet backslash). Daarna wrap caller zelf `%term%` voor wildcard-matching.

### 4.4 `format_response(data, summary=None, error=None) -> dict`

Consistente shape:

```python
{"data": [...],       # altijd list
 "summary": {...},    # tool-specifieke dict of None
 "error": "..."}      # string of None
```

## 5. Tool Specs

### T1 — `search_products(search_term: str, limit: int = 20, company: str | None = None)`

**SQL strategie** (exacte kolomnamen tijdens impl verifiëren):
```sql
SELECT TOP (?) Productcode, Searchname, Make, Productgroup, Salesprice, InActiveProgram
FROM PRODUCT
WHERE (Productcode LIKE ? OR Searchname LIKE ?)
ORDER BY InActiveProgram ASC, Salesprice DESC
```

- `limit` clamped 1–500
- `search_term` door `sanitize_like` en daarna `%term%`
- `company` via `company_filter`
- **Output:** `data` = lijst van product-dicts; `summary` = `{count, search_term}`

### T2 — `get_revenue(start_date: str, end_date: str, company: str | None = None, group_by: str = "total")`

**Params:**
- `start_date`, `end_date` — ISO, via `admcms_date` naar char(8)
- `group_by` — enum: `"total"`, `"month"`, `"make"`, `"customer"`, `"company"`; invalid waarde → `{"error": "Invalid group_by..."}`

**SQL strategieën:**
- `total`: `SELECT SUM(TotalAmount), COUNT(*) FROM SALESINVOICE WHERE InvoiceDate BETWEEN ? AND ?`
- `month`: `GROUP BY SUBSTRING(InvoiceDate, 1, 6)` — retourneert `{period: "202603", amount: ...}`
- `make`: `JOIN SALESINVOICELINES JOIN PRODUCT GROUP BY Make` (exacte join-kolom verifiëren)
- `customer`: `JOIN CUSTSUPPLIER GROUP BY Searchname` (exacte foreign key verifiëren)
- `company`: `GROUP BY Company` (company-kolom verifiëren)

**Output:** `data` = breakdown rows (of 1 row voor total); `summary` = `{total_amount, invoice_count, period: "start..end", group_by}`

### T3 — `get_customer_orders(customer_id: int | None = None, only_open: bool = True, company: str | None = None, limit: int = 50)`

**SQL strategie:**
```sql
SELECT TOP (?) soh.Ordernr, soh.CustSuppnr, cs.Searchname,
       soh.OrderDate, soh.ShippingDate, soh.TotalAmount, soh.Status
FROM SALESORDERHEADER soh
LEFT JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = soh.CustSuppnr
WHERE (? IS NULL OR soh.CustSuppnr = ?)
  AND (... only_open logic ...)
ORDER BY soh.OrderDate DESC
```

**Open-filter uitwerken tijdens impl** — afhankelijk van Status-kolom waarden (moet de implementer `lookups SALESORDERHEADER Status` draaien om toegestane waarden te vinden, of `get_distinct_values`).

- `customer_id = None` → alle klanten
- `only_open = True` → filter op niet-gefactureerde orders; exacte SQL-conditie impl-tijd bepaald
- **Output:** `data` = order rows; `summary` = `{count, total_open_value, customer_id}`

## 6. Server Registratie

In `build_app` → `_build_mcp_server`:

```python
@mcp.tool()
def search_products(search_term: str, limit: int = 20, company: str | None = None) -> dict:
    """Zoek producten in ADMCMS op Productcode of Searchname.

    Args:
        search_term: zoek-string, matcht op Productcode of Searchname (case-insensitive, gebruikt LIKE).
        limit: max aantal resultaten (1-500), default 20.
        company: optioneel; filter op specifieke company/mandant. None = alle bedrijven.

    Returns dict met keys: data (list[dict]), summary (dict), error (str | None).
    """
    return _search_products(search_term, limit=limit, company=company)
```

Idem voor T2 en T3. Docstrings zijn Engels-met-Nederlandse-voorbeelden want Claude leest die om de tool te gebruiken.

## 7. Error Handling

Consistent met bestaande tools in `tools.py`:

```python
def search_products(search_term: str, limit: int = 20, company: str | None = None) -> dict:
    try:
        # validate + build SQL + execute
        return format_response(data=..., summary=...)
    except ValueError as e:
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("search_products failed")
        return format_response(data=[], error=f"Execution error: {e}")
```

Nooit raisen — MCP-clients verwachten een dict terug zelfs bij fouten.

## 8. Testing Strategy

`tests/test_query_helpers.py`:

- `admcms_date` — 3 tests: valid, wrong format, garbage input
- `company_filter` — 2 tests: None input → empty, string input → SQL fragment
- `sanitize_like` — 4 tests: each metacharacter (`% _ [ ]`)
- `format_response` — 3 tests: data only, with summary, with error

`tests/test_tools.py` uitbreiden:

- `TestSearchProducts` — 5 tests: happy path, empty results, sanitize_like werkt, SQL injection attempt via search_term, company filter werkt
- `TestGetRevenue` — 7 tests: elk `group_by` (5), invalid group_by, invalid date format
- `TestGetCustomerOrders` — 4 tests: met/zonder customer_id, `only_open=True/False`, company filter

Alle tests mocken `admcms_mcp.tools.execute_query` — geen live DB.

## 9. Schema-Verification Protocol

De implementer MOET voor onzekere kolom-namen (company-kolom, status-kolom, join-keys) **eerst verifiëren** vóórdat SQL hard-coded:

1. Check `SCHEMA_KNOWLEDGE` constant in `schema.py`
2. Zo niet gevonden: gebruik `get_schema(table_name)` tegen de live DB (lokaal met env-var override of tegen staging)
3. Zo niet helder: vraag de user

**Gokken = bug die pas in productie naar boven komt.** De 5-min extra tijd voor verificatie is altijd verdiend.

## 10. Extensibility — Hoe voeg ik straks T4/T5/T6 toe?

Na deze MVP is het patroon vast:

1. Schrijf failing test in `tests/test_tools.py` (`TestToolName`)
2. Schrijf implementation in `tools.py`, hergebruik `query_helpers.py`
3. Registreer in `server.py` met `@mcp.tool()` + goede docstring
4. Run tests → groen → commit
5. Uvicorn `--reload` → tool direct beschikbaar in MCP
6. Eventueel `/mcp` refresh in Claude Desktop/Code

Geen nieuwe modules of architectonische beslissingen nodig voor volgende tools (tenzij we expliciet een nieuw patroon willen).

## 11. Out of Scope (v1.1+)

- **T4 — `get_stock_levels`** — onzeker welke tabel stock bevat; eerst schema-ontdekking
- **T5 — `get_shipping_summary`** — shipping kolommen onzeker; eerst schema-ontdekking
- **Leverancier-analyse** — `CUSTSUPPLIER where type='S'`; wacht op vraag-patroon
- **Customer-detail tool** (krediet, VAT, laatste bestelling) — opgelost met huidige `query_database` tot vraag breder wordt
- **Paginatie** — voor nu geen `offset` param; `limit`-only. Toevoegen als users echt 500+ rijen lopen ophalen
- **Cache** — elke call hit de DB; overweeg cache als dezelfde query vaak voorkomt

## 12. Acceptatie-Criteria

Iteratie is klaar als:

- [ ] `query_helpers.py` bestaat, 4 functies met >85% coverage
- [ ] 3 nieuwe tool-functies in `tools.py`, geregistreerd in `server.py`
- [ ] `pytest -v` toont oude 60 tests + ~20 nieuwe tests, alle groen
- [ ] `ruff check && ruff format --check` clean
- [ ] Lokaal end-to-end getest: `search_products`, `get_revenue`, `get_customer_orders` tegen echte ADMCMS replica (via `scripts/test_mcp_client.py`)
- [ ] README.md updated met tool-lijst
- [ ] Feature branch gemerged naar main, gepusht naar GitHub

## 13. Referenties

- Bestaand: [docs/superpowers/specs/2026-04-18-admcms-mcp-remote-design.md](2026-04-18-admcms-mcp-remote-design.md) — overall architectuur
- Bestaand: [docs/DATABASE-KENNIS/ADMCMS_Query_Cookbook.md](../../DATABASE-KENNIS/ADMCMS_Query_Cookbook.md) — SQL voorbeelden
- Bestaand: [src/admcms_mcp/schema.py](../../../src/admcms_mcp/schema.py) — SCHEMA_KNOWLEDGE constant
- MCP spec 2025-03-26 — tool annotation format
