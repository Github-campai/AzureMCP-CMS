# Changelog

Noemenswaardige wijzigingen aan AzureMCP-CMS. Nieuwste bovenaan.

De doorlopende beschrijving van *hoe het werkt* staat in
[`docs/ARCHITECTUUR.md`](docs/ARCHITECTUUR.md); dit bestand vertelt *wat er is
veranderd en waarom*.

---

## 2026-08-18 / 19 — OAuth, autorisatie, audit en CI

Grote ronde. Hieronder eerst wat het voor je dagelijkse werk betekent, daarna per
onderwerp wat er is gebeurd.

### Voor Roland: wat verandert er voor jou

Je werkwijze blijft hetzelfde — tool schrijven in `src/admcms_mcp/tools.py`,
registreren in `server.py` met `@mcp.tool()` + `_audited(...)`, pushen naar
`main`, deploy loopt vanzelf. Vier dingen zijn wél anders:

1. **Eerst `git pull`.** Er staan 20 commits op `main` van 18 en 19 augustus.
2. **Een push die faalt op tests of lint wordt niet gedeployed.** De
   deploy-workflow roept `ci.yml` aan en hangt daarachter. Draai dus lokaal
   `pytest -q` en `ruff check . && ruff format --check .` voor je pusht.
   Momenteel 357 tests.
3. **De databaselogin is gewijzigd.** De MCP verbindt niet meer als `saazuredb`
   (server-admin over vier databases) maar als `admcms_mcp` met alleen
   `db_datareader` op `ADMCMS-PRODUCTION-REPL01`. Als je een tool schrijft die
   iets anders dan lezen wil, faalt dat nu — en dat is de bedoeling.
4. **Documentatieplicht.** `CLAUDE.md` schrijft voor dat een commit die auth,
   tools, config of deploy raakt `docs/ARCHITECTUUR.md` in dezelfde commit
   bijwerkt. Die regel is niet van mij, maar hij is wel gevolgd in deze ronde.

Nieuw handig voor jezelf: de tool **`describe_server`** vertelt wat de server is,
wie je bent, welke tools er zijn (inclusief per tool of hij filters en paginatie
ondersteunt), de limieten en de valkuilen in de data. Afgeleid uit het live
tool-register, dus die kan niet achterlopen.

### Inloggen met alleen een URL

Clients die niets anders krijgen dan een URL liepen dood op discovery: Entra
publiceert geen `registration_endpoint`, en daar weigeren Claude, Cursor en
VS Code de flow op te starten.

De server adverteert zich nu zelf als authorization server en proxyt door naar
Entra ([`oauth.py`](src/admcms_mcp/oauth.py)): RFC 9728- en RFC 8414-metadata,
een `/register`-stub die de bestaande Entra app-id teruggeeft, en `/authorize`
plus `/token` als proxy met scope-rewrite. Entra blijft de enige identity
provider; er worden hier geen tokens gemaakt.

Ook gerepareerd: `WWW-Authenticate` wees naar een `http://`-URL (hij las
`request.url.scheme`, en achter de ingress is dat plain http), en
`/.well-known/oauth-protected-resource/mcp` gaf 401 terwijl dat het pad is dat
clients als eerste proberen.

**URLs.** De server antwoordt op twee namen, beide met managed certificate:

- `https://admcms.campai.nl/mcp` — Campai
- `https://mcp-cms-adm.cmsnl.com/mcp` — klantnaam

Elke hostname beschrijft zichzelf in de metadata (`PUBLIC_BASE_URL` +
`PUBLIC_BASE_URL_ALIASES`); een onbekende Host-header valt terug op de canonieke
URL, zodat niemand de server een vreemd domein kan laten adverteren.

### Autorisatie: groepen werken nu echt

`groupMembershipClaims` stond op `null`, dus Entra stuurde **geen** `groups`-claim
mee. De groepstak in `check_access` matchte daardoor nooit en elke gebruiker moest
als los object-id in Key Vault. De onboarding-belofte dat CMS' IT zelf mensen
toevoegt, werkte dus niet.

Waarom dat niemand opviel: `tests/test_auth_allowlist.py` injecteert zélf een
`groups`-claim en controleert dan dat toegang wordt verleend. De logica was
getest — tegen een tokenvorm die Entra nooit verstuurt.

Nu `ApplicationGroup` (bewust niet `SecurityGroup`: die zet álle groepen in het
token en kapt boven de Entra-limiet af tot een verwijzing, waarna groepstoegang
stil stopt). Toegewezen aan de enterprise app: `SG-Campai-ADMCMS-Users` in de
Campai-tenant, `SG-CMS-MCP-Users` in de CMS-tenant.

**Iemand toevoegen = in de juiste groep zetten.** Geen Key Vault meer nodig.
Script: [`scripts/enable-group-claims.ps1`](scripts/enable-group-claims.ps1).

### `/admin/*` alleen voor Campai

De admin-endpoints geven de audit-trail van *iedere* gebruiker terug: upn's en de
SQL die hun vragen opleverden. Iedereen op de allowlist kon dat lezen, dus een
CMS-eindgebruiker kon zien wat Campai had opgevraagd.

`check_admin_access()` draait nu in de middleware, alleen voor `/admin`.
Ingesteld via een `admins`-sectie in dezelfde Key Vault-allowlist, dus rechten
geven of intrekken is een secret-wijziging zonder deploy. Faalt dicht: geen
configuratie betekent dat niemand erbij komt. Het is geen omweg — `check_access`
draait eerst.

### Audit overleeft de nacht

De audit-log was SQLite in `/tmp`: verdeeld over max 3 replicas en elke nacht om
23:06 gewist door de restart. De README belooft een audit-entry per tool-call, dus
wat er stond was erger dan niets — het leek bewijsmateriaal en was het niet.

`AzureTableStore` was een stub die `NotImplementedError` gooide en is nu echt
([`audit_stores.py`](src/admcms_mcp/audit_stores.py)). Storage account
`cmsmcpauditprod39b6`, bereikt met de bestaande managed identity — geen key om te
roteren. Zelfde `AuditStore`-protocol, dus `/admin/audit` en `/admin/stats`
werken onveranderd.

### Resultaten worden niet meer stil afgekapt

`cursor.fetchmany(n)` geeft n rijen terug of het resultaat op was óf afgekapt —
niet te onderscheiden. En `max_rows` werd geklemd op 500, dus twee tools die om
2000 rijen vroegen kregen er stil 500.

Beide querypaden halen nu één rij méér op dan de limiet als probe, knippen die
eraf en zetten een vlag. Die komt via `_audited` in **élk** toolantwoord terecht
als `"truncated": true` plus een hint. Harde limiet 500 → 5000, instelbaar via
`MAX_ROWS_HARD_LIMIT`.

`get_filter_options` was het ergst: top-30/50/100 in de SQL gebakken zonder
signaal. Die meldt nu `complete`, noemt welke dimensies afgekapt zijn, en met
`max_per_dim=1000` krijg je alles.

### Twee echte bugs onderweg

- **Reflected XSS op de publieke `/`**: `tenant`, `error` en `error_description`
  uit de querystring gingen ongeëscaped in de HTML. Nu `html.escape`.
- **Een mislukte filter-refresh werd 6 uur als geldig gecached.**
  `time.monotonic()` telt vanaf boot, en `ts = 0.0` als "verouderd"-marker leest
  dan als *vers* op elk proces jonger dan de TTL — wat door de nachtelijke
  restart elke nacht zo is. CI vond dit: de test slaagde op een langlopende
  machine en faalde op een verse runner.

### Deploy zou stuk zijn gegaan

`mcp[cli]>=1.2.0` had geen bovengrens, `mcp` 2.0 verwijderde
`mcp.server.fastmcp`, en de Dockerfile doet `pip install .` (de `uv.lock` werd
genegeerd). De volgende build had een container gemaakt die niet start; lokaal
gereproduceerd. Alle dependency-ranges hebben nu een bovengrens per major.

### CI en security

- `ci.yml` — ruff, pytest met coverage, `pip-audit`, plus een import-check met de
  daadwerkelijk geresolveerde dependency-set. De deploy hangt hierachter.
- `security-scan.yml` — Gitleaks, Trivy, Semgrep, ook nachtelijk.
- `dependabot.yml` — wekelijks pip, docker, actions.
- Post-deploy verificatie loopt niet meer alleen langs `/healthz` (die blijft 200
  terwijl niemand kan inloggen) maar langs de hele OAuth-keten.
- Container draait als non-root.

### Database-login versmald

De MCP verbond als `saazuredb`, de **server-administrator** van `cmsnl` — een
server met vier databases. Voor 25 read-only tools is `db_datareader` op één
database genoeg. Nieuwe login `admcms_mcp` aangemaakt, Key Vault bijgewerkt,
revisie herstart, geverifieerd met een echte tool die tabellen leest.
Script: [`scripts/rotate-db-credentials.ps1`](scripts/rotate-db-credentials.ps1).

**Nog te doen**: het wachtwoord van `saazuredb` stond letterlijk in
`docs/service-handleiding.md` en zit nog in de git-historie (commit `2288f15`).
Uit de docs gehaald, maar redigeren is niet de oplossing — dat wachtwoord moet
geroteerd worden in overleg met de leverancier, die volgens de documentatie
eigenaar van die login is.

### Logo

Het CMSNL-logo staat nu naast de connector, via `serverInfo.icons` (MCP-spec) en
op `/icon.png`, `/favicon.ico` en `/apple-touch-icon.png`. Vervangen = het bestand
`src/admcms_mcp/assets/icon.png` overschrijven en pushen; het formaat wordt uit de
bytes gelezen, niet uit de extensie.

---

## Nog open

| Wat | Wie |
|---|---|
| `saazuredb`-wachtwoord roteren (staat nog in de git-historie) | Campai + leverancier |
| `AllowAllWindowsAzureIps` in de SQL-firewall vervangen door `50.85.29.106` | Campai + leverancier |
| Copilot-route voor Joost (bestaande Copilot Studio-connector of MCP rechtstreeks) | keuze CMS |
| Nachtelijke restart: oorzaak nooit onderzocht, staat er als workaround | Campai |
| `health_check.tool_count` staat hardcoded op 23 terwijl er 25 tools zijn | Campai |
| SQL-auditing bewaart 7 dagen — kort als je wilt weten of het gelekte wachtwoord gebruikt is | Campai |
