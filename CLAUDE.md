# CLAUDE.md — werkafspraken voor dit repo

## Wat dit is

Remote MCP-server (Python 3.12, FastMCP/Starlette) op de ADMCMS-database van CMS Lelystad,
gehost door Campai in Azure Container Apps. Entra ID multi-tenant OAuth; 25 read-only tools.
Volledige beschrijving: **[`docs/ARCHITECTUUR.md`](docs/ARCHITECTUUR.md)** — lees die eerst.

## Levende documentatie — verplicht bijwerken

`docs/ARCHITECTUUR.md` is de bron van waarheid over auth, autorisatie (tenants/groepen/allowlist),
endpoints, tools, data-laag, audit, config en deploy. **Elke commit die iets daaraan verandert,
werkt dat document in dezelfde commit bij** (zie §12 daar voor de mapping wijziging → sectie).
Niet vergeten:

- Tool erbij/anders/weg → §6.1 tabel + `README.md` tool-tabel + `health_check.tool_count` in `server.py`.
- Auth/OAuth/allowlist/group/tenant → §4/§5 (+ `docs/DEPLOYMENT.md`, `docs/ONBOARDING-CUSTOMER.md`).
- Env-var/secret → §9. Deploy/CI/infra → §10. Afwijking gevonden → §11.
- Regel "Laatst geverifieerd tegen commit …" bovenaan bijwerken.

## Werkwijze

- Tests: `pytest -q` (357 tests) en `ruff check . && ruff format --check .` moeten groen zijn vóór commit; CI blokkeert deploy anders.
- Nieuwe tool: pure functie in `src/admcms_mcp/tools.py` (parametriseer via `execute_query_params`, `format_response`), registreren in `server.py` via `@mcp.tool()` + `_audited(...)`, tests in `tests/test_tools.py`.
- Nooit `AUTH_DISABLED=true` in productie-config; nooit secrets in code/env-yaml — alles via Key Vault (`keyvault.get_secret`).
- Push naar `main` deployt automatisch (paden `src/**`, `Dockerfile`, `pyproject.toml`, `uv.lock`); documentatie-commits deployen niet.
- Taal: documentatie en commit-omschrijvingen in het Nederlands, code/docstrings in het Engels (bestaande conventie).
