# Changelog

Noemenswaardige wijzigingen aan AzureMCP-CMS. Nieuwste bovenaan.

De doorlopende beschrijving van *hoe het werkt* staat in
[`docs/ARCHITECTUUR.md`](docs/ARCHITECTUUR.md); dit bestand vertelt *wat er is
veranderd en waarom*.

---

## 2026-08-27 — Opruiming: `CAMPAI_ALLOWED_GROUP` weg

Een tweede manier om dezelfde beslissing te nemen: naast de allowlist in Key
Vault kon één Entra-groep via de env-var `CAMPAI_ALLOWED_GROUP` rechtstreeks
toegang geven. Die variabele stond niet in de bicep en is in productie nooit
gezet, dus het pad werd nooit doorlopen — de Campai-groep komt gewoon binnen via
de `groups`-lijst van de allowlist.

Weg dus. Het scheelt een tweede plek om te kijken als iemands toegang niet klopt.
Omdat de env-var nergens gezet is, verandert er in productie niets: de conditie
begon met `settings.campai_allowed_group and …` en die was altijd leeg.

De test die het oude pad afdekte toetst nu de route die werkelijk gebruikt
wordt (groep in de allowlist), en er is een test bij die vaststelt dat het
zetten van `CAMPAI_ALLOWED_GROUP` niets meer doet.

---

## 2026-08-27 — Hardening ronde 4: injectie, groepen, CORS, SQL-check en opruiming

### Databasetekst ging ongemarkeerd naar het model

Een productomschrijving of klantnaam in ADMCMS is door een mens ingetypt, en een
model dat zo'n veld leest kan er een instructie in vinden. Tools gaven die
tekst als kale JSON terug, en de server gaf helemaal geen `instructions=` mee.

`server._audited` markeert nu de vrije-tekstkolommen (`Searchname`,
`Description`) van elke `data`- en `rows`-lijst met `⟦extern⟧…⟦/extern⟧`, en de
server-instructions leggen uit dat wat daartussen staat gegevens zijn en nooit
een opdracht. De formulering is overgenomen van de Campai Securitech-MCP, zodat
een client die met beide praat één afspraak leert in plaats van twee.

Bewust kort gehouden: codes, datums, bedragen en magazijnletters kunnen geen zin
bevatten, en waarden die bedoeld zijn om terug te geven aan een tool
(`get_lookups`, `get_filter_options`) worden níét gemarkeerd — een gemarkeerde
filterwaarde matcht nergens meer op.

### Een gebruiker in te veel groepen viel stil om

Boven ~200 groepslidmaatschappen laat Entra de `groups`-claim weg. Zo iemand
kwam alleen nog via de user-lijst binnen, en een beheerder die zijn toegang aan
een groep ontleende werd geweigerd met "User not on allowlist" — een melding die
niets uitlegt. De overage wordt nu herkend (`_claim_names` / `hasgroups`),
gelogd, en de weigering zegt wat er aan de hand is en hoe het op te lossen.
Ook opgeschreven in `docs/ONBOARDING-CUSTOMER.md`, want dit gaat bij een grote
klanttenant een keer gebeuren.

### CORS stond op `*`

Elke website kon `/admin` en `/mcp` vanuit de browser van een bezoeker
aanroepen. Er is geen cookie, dus een aanvaller heeft nog steeds een bearer
token nodig — maar een token dat in JavaScript belandt hoort niet ook nog
toestemming te krijgen om overal vandaan gebruikt te worden. Nu: de eigen
hostnames plus `null` (de admin-UI over `file://`). `CORS_ALLOW_ORIGINS=*` zet
het oude gedrag terug als een browserclient er toch over valt; lokaal zonder
`PUBLIC_BASE_URL` blijft het `*`.

### `validate_query` weigerde data en liet structuur door

Twee kanten van dezelfde fout: `;` werd niet geweigerd (stacked statements
werden alleen indirect gevangen), terwijl een legitieme query met "update" in
een stringliteral juist wél sneuvelde — zonder dat de melding uitlegde waarom.

Nu wordt de check gedaan op de query met de inhoud van `'…'`-literals
leeggemaakt: een product dat "update kit" heet is geen UPDATE-statement, en
`;` mag alleen nog de query afsluiten. De foutmeldingen zeggen wat er geweigerd
is en hoe het wél kan, en `ARCHITECTUUR.md` §7 legt vast dat de databaserechten
(`db_datareader`) de primaire waarborg zijn en deze check de tweede laag.

### Opruiming

- `health_check.tool_count` stond hardcoded op 23 terwijl er 25 tools zijn. Het
  is nu `len(await mcp.list_tools())`, dus het kan niet meer uit de pas lopen.
- Test-aantallen gelijkgetrokken: README zei 279, CLAUDE.md 357. Het zijn er
  442. `docs/TESTING.md` zei 21 tools, dat zijn er 25.
- `keyvault._env_override` liet secrets uit de omgeving komen, ook in
  productie. Nu achter `ALLOW_ENV_SECRETS` (default uit); productie zet geen van
  die variabelen, dus daar verandert er niets.

Tests: 27 nieuwe (`tests/test_hardening_small.py`, uitbreiding van
`tests/test_db_validator.py`).

---

## 2026-08-27 — Hardening ronde 3: weerbaarheid

### De server bevroor bij elke zware vraag — en Azure herstartte hem daarom

Dit is de oorzaak van de disconnects die al een tijd gemeld worden, en hij stond
niet in de reviewlijst.

De MCP SDK roept een **synchrone** tool-functie rechtstreeks op de event loop
aan (`func_metadata.call_fn_with_arg_validation`: `if fn_is_async: await fn()
else: fn()`). 24 van onze 25 tools waren synchroon, en een pyodbc-query mag 180
seconden duren. Al die tijd antwoordde het proces niets — geen tweede request,
geen `/healthz`. Azure leest dat als een dode container:

```
09:05:08  CallToolRequest  (een gebruiker stelt een vraag)
09:05:32  Readiness probe failed: /healthz context deadline exceeded
09:06:44  Container admcms-mcp failed liveness probe, will be restarted
09:07:15  Container created
```

13 van die `ReplicaUnhealthy`-events op 27 augustus alleen al. Elke restart
verbreekt de verbinding van iedereen die op dat moment verbonden is, en degene
die de vraag stelde krijgt nooit antwoord.

`_audited` is nu async en draait het werk via `asyncio.to_thread`; alle
tool-handlers zijn `async def`. Ook de opmerking in `db.py` dat "the MCP runs
sync tool handlers in a threadpool" klopte niet — vandaar dat de connection pool
die eromheen gebouwd is het probleem nooit heeft opgelost.

Dezelfde behandeling voor de auth-middleware: `validate_jwt`, `check_access` en
`check_admin_access` doen blokkerende netwerk-I/O (JWKS via synchrone httpx, de
allowlist via de Key Vault-SDK) en draaien nu in een worker-thread. Beide caches
hebben er een single-flight lock bij gekregen: de allowlist verloopt elke vijf
minuten, en zonder die lock stuurde elke gelijktijdige request bij zo'n miss zijn
eigen Key Vault-call.

### Eén gebruiker kon de server voor iedereen bezet houden

`query_database` is vrije SQL met 180 seconden timeout, op acht connecties en een
halve vCPU. Nieuw `limits.py`: maximaal **3** gelijktijdige tool-calls per
gebruiker (`MAX_CONCURRENT_QUERIES_PER_USER`, `0` = uit).

Boven de limiet **wacht** een call tot 60 seconden
(`QUERY_QUEUE_TIMEOUT_SECONDS`) in plaats van meteen te falen — een dashboard
opent legitiem meerdere tegels tegelijk en die horen even te wachten. Pas als de
wachttijd zelf oploopt volgt een weigering, in woorden waar iemand iets mee kan,
en die weigering staat ook in de audit. Een teller per minuut is bewust niet
toegevoegd: het probleem is queryduur, niet frequentie.

De staat leeft per replica in het geheugen. Met `maxReplicas: 3` is de feitelijke
bovengrens dus hoger als iemands requests op verschillende replica's landen; dit
is een rem tegen het verzadigen van één server, geen quotum.

### De query-cache groeide onbegrensd

`_query_cache` was een dict zonder eviction, gesleuteld op `(sql, params,
row cap)` — met vrije SQL erboven een oneindig aantal sleutels, in een container
van 1 GiB. Nu een LRU van 256 entries; verlopen entries verdwijnen bij een miss.

`query_database` cachet niet meer (elke vrije query is uniek, dus de cache
verzamelde alleen sleutels die nooit hergebruikt werden) en de `SELECT 1` van
`health_check` ook niet — die meldde tien minuten lang een gezonde database
nadat die was uitgevallen.

De cachesleutel bevat bewust geen gebruiker; dat is vandaag correct en staat nu
vastgelegd in een test, zodat het opvalt op de dag dat een tool per klant gaat
filteren.

Tests: 20 nieuwe (`tests/test_resilience.py`, `tests/test_query_cache.py`),
waaronder een die `/healthz` opvraagt terwijl er een trage tool draait — precies
wat Azure's liveness-probe doet.

---

## 2026-08-27 — Hardening ronde 2: de audit-trail vastgelegd in infra

### De bicep wist niet wat er in Azure draait

`infra/resources.bicep` zette `AUDIT_STORE=local` met `AUDIT_DB_PATH=/tmp/audit.db`,
terwijl de container app in productie allang op `azure_table` staat — met een
storage account (`cmsmcpauditprod39b6`), een table (`admcmsaudit`) en een
*Storage Table Data Contributor*-toewijzing voor de managed identity, allemaal
met de hand aangemaakt en nergens vastgelegd.

Er was dus niets kapot, maar de eerstvolgende `az deployment group create` had
prod teruggezet naar een SQLite-bestand in `/tmp`: per replica een eigen trail,
en elke restart weg. Alles staat nu in de bicep, inclusief acht tests die
precies op die drift falen.

Twee bewuste keuzes daarbij:

- De accountnaam is een **parameter** met de live waarde als default, geen
  afleiding van `resourceToken`. Het account bestond al; een afgeleide naam zou
  een tweede account aanmaken naast het bestaande.
- `allowSharedKeyAccess: false`. De app authenticeert met zijn managed identity
  en niets in dit repo gebruikt een sleutel of connection string voor dit
  account (nagekeken). Dit is de enige regel die een live resource wijzigt.

### Bewaartermijn is nu een keuze in plaats van "voor altijd"

Een audit-rij bevat de UPN van degene die de query draaide. Die onbeperkt
bewaren mag niet zonder doel om naar te wijzen. Termijn: **365 dagen**
(`AUDIT_RETENTION_DAYS`, `0` = uit). Een jaar dekt de rapportagecyclus waar deze
cijfers bij horen; korter en de trail kan niets meer zeggen over het vorige
kwartaal. De onderbouwing staat in `docs/ARCHITECTUUR.md` §8.

Opruimen doet de app zelf: `_audit_retention_loop()` draait bij het starten en
daarna elke 24 uur, in een worker-thread. Geen extra job en geen extra rechten —
de container heeft de identity al. In Table Storage snijdt de purge op
`PartitionKey` (de UTC-datum), dus er worden geen rijen gelezen om te bepalen
wat weg moet.

### Nachtelijke restart: oorzaak gevonden

Stond in de vorige changelog als "oorzaak nooit onderzocht". De container wordt
**overdag** al herstart door Azure zelf: `/healthz` antwoordt niet meer terwijl
een trage query de event loop blokkeert, waarna de liveness-probe hem neerhaalt
(`ReplicaUnhealthy` → *failed liveness probe*, 13× op 27-08 volgens
`ContainerAppSystemLogs`). De nachtelijke restart lost dat niet op. Advies:
laten staan tot de event-loop-blokkade en de cache-eviction zijn opgelost, dan
verwijderen. Voor de audit is hij niet langer schadelijk.

Tests: 22 nieuwe (`tests/test_audit_retention.py`, `tests/test_infra_audit_config.py`).

---

## 2026-08-27 — Hardening ronde 1: OAuth-proxy en foutmaskering

### De OAuth-proxy brokerde meer dan we adverteren

`/authorize` gaf de querystring één op één door aan Entra en `/token` de hele
form-body. Alleen `resource` werd verwijderd en `scope` herschreven. Daarmee
was `/authorize` op `admcms.campai.nl` een open relay: een aanvaller bouwt een
inlog-URL op ons domein die consent vraagt voor zijn eigen app, en de gebruiker
ziet onze hostname in de adresbalk. Voor `/token` gold hetzelfde voor grant
types die we nooit hebben bedoeld te ondersteunen.

- `client_id` moet nu de app-id zijn die `/register` uitdeelt (of de
  `api://`-vorm). Een afwijkende waarde geeft `400 invalid_request` — een
  weigering, geen stille correctie, want een client die iets anders stuurt heeft
  een configuratieprobleem dat je wilt zien.
- `grant_type` is beperkt tot `authorization_code` en `refresh_token`, precies
  wat de metadata adverteert. `password` en `client_credentials` halen Entra
  niet meer.
- `redirect_uri` toetsen we bewust niet aan een eigen lijst; de afweging staat
  in `docs/ARCHITECTUUR.md` §4.2.

### Rauwe databasefouten gingen naar de klant

Op 22 plekken in `tools.py` en in de DB-ping van `health_check` ging
`f"Execution error: {e}"` terug naar de client. pyodbc noemt daarin de driver,
de servernaam (`cmsnl.database.windows.net`) en soms een stuk SQL — dat belandde
in het gespreksvenster van de klant en in de logs van hun MCP-client.

Nieuw: `src/admcms_mcp/errors.py`. `client_error(exc)` zet de volledige tekst in
het log en op de audit-entry, en geeft de client een neutrale zin met een
referentie. Die referentie is de id van de audit-entry, dus
`GET /admin/audit/{referentie}` levert het hele verhaal — de beheerder kan het
opzoeken, de klant ziet het niet.

`health_check` draait zijn databaseprobe nu binnen `_audited`, anders had een
mislukte ping geen audit-entry om naar te verwijzen.

Tests: 13 nieuwe (`tests/test_errors.py`, uitbreiding van `tests/test_oauth.py`).
Drie bestaande tests in `tests/test_tools.py` toetsten op de rauwe tekst en
toetsen nu op de afwezigheid ervan.

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
