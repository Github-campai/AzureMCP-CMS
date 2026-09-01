# Changelog

Noemenswaardige wijzigingen aan AzureMCP-CMS. Nieuwste bovenaan.

De doorlopende beschrijving van *hoe het werkt* staat in
[`docs/ARCHITECTUUR.md`](docs/ARCHITECTUUR.md); dit bestand vertelt *wat er is
veranderd en waarom*.

---

## 2026-09-01 - Copilot mag een eigen audience meebrengen

Microsoft 365 Copilot stuurt geen token met onze audience. De Teams Developer
Portal munt bij een SSO-registratie een eigen Application ID URI in de vorm
`api://auth-<registratie-guid>/<app-id>`, en dat is de `aud` van het token dat
Copilot namens de gebruiker stuurt. `validate_jwt` accepteerde alleen het app-id
en `api://<app-id>`, dus elk Copilot-verzoek zou een 401 `Wrong audience` zijn
geworden - nog voor de allowlist iets te zeggen had.

`ENTRA_EXTRA_AUDIENCES` (komma-gescheiden) zet zulke waarden erbij. Bewust een
expliciete lijst en geen patroon op `api://auth-*`: elke waarde hier is een
extra publiek dat onze tokens mag dragen, en dat hoort een beheerder een voor
een op te schrijven in plaats van dat een wildcard het stilletjes goedkeurt.

Leeg laten verandert niets - een test bewaakt dat ook.

De gegenereerde URI hoort daarnaast in `identifierUris` van de app-registratie,
en de Microsoft Enterprise token store (`ab3be6b7-...`) moet pre-geautoriseerd
zijn op `admcms.read`. Beide zijn Entra-werk, geen code.

---

## 2026-08-31 — `mcp` terug op `<2`: de 2.x-bump brak de hele server

Dependabot verruimde de constraint van `mcp[cli]<2` naar `<3`. CI installeert
met `pip install -e ".[dev]"`, niet uit de lockfile, en pakte dus `mcp 2.1.1`.
Daarop startte de server niet meer:

    ModuleNotFoundError: No module named 'mcp.server.fastmcp'
    This is mcp 2.x, where FastMCP was renamed to MCPServer

Elf testmodules faalden al bij het importeren, de deploy stopte, en zowel die
PR als de eerstvolgende merge kwam niet in productie. De draaiende revisie is
al die tijd gezond gebleven — er is niets kapot gedeployd, er is alleen niets
gedeployd.

Constraint terug op `<2`, met de reden in de regel zelf.

Belangrijker is de tweede helft: Dependabot krijgt nu een `ignore` voor
major-updates van `mcp`. Een major van deze afhankelijkheid is geen update maar
een migratie — `FastMCP` heet `MCPServer` en zit in een andere module, dus
`server.py` moet er in zijn geheel doorheen. Zoiets hoort niet ongemerkt via
een dependency-PR binnen te komen. Patch- en minor-updates komen gewoon door.

De migratie naar mcp 2.x blijft staan als werk voor later.

---

## 2026-08-31 — Gasten kunnen erin: de authority hangt nu aan de hostname

Een gast van CMS Lelystad kwam er niet in. Hij meldde zich netjes aan bij
Microsoft, kreeg een token, en werd vervolgens bij elke call geweigerd.

De oorzaak zit in één woord in de aanmeld-URL. `/authorize` stuurde iedereen
naar `login.microsoftonline.com/organizations`, en dat betekent letterlijk: "een
willekeurige werk-tenant — zoek zelf uit waar deze persoon thuishoort". Voor een
medewerker klopt dat. Voor een gast niet: die hoort thuis bij zijn eigen
werkgever, dus daar meldt Entra hem aan. Zijn token draagt die tenant,
`validate_jwt` weigert hem op `Untrusted tenant`, en zijn lidmaatschap van
`SG-CMS-MCP-Users` komt nooit aan bod.

Met GDAP in de klanttenant nagekeken, en daar bleek alles al goed te staan:

- `appRoleAssignmentRequired` op de app: `false`
- de gast: `accountEnabled`, uitnodiging geaccepteerd, en **al lid** van
  `SG-CMS-MCP-Users`
- zijn aanmeldgeschiedenis daar: twintig keer Power BI, geslaagd — en **geen
  enkele** aanmelding voor `CMS-MCP-API`, terwijl hij het die avond twee keer
  probeerde

Die laatste regel is het bewijs: zijn pogingen kwamen nooit in die tenant aan.
En Power BI werkt wél voor hem omdat dat een tenant-specifieke aanmeldlink
uitdeelt — precies wat wij niet deden.

`authority_for_request()` kiest de authority nu op hostname, via
`ENTRA_AUTHORITY_BY_HOST` (`host=tenant-id`, komma-gescheiden). De
klanthostname meldt aan bij de klanttenant, de onze blijft op `organizations`.
Per hostname en niet globaal, want één authority voor allebei zou de andere
partij uit haar eigen directory duwen.

Drie dingen die bij het bouwen de aandacht vroegen:

- `/authorize` en `/token` leiden de authority allebei uit dezelfde request af.
  Lopen ze uiteen, dan is de code niet inwisselbaar en breekt élke aanmelding —
  niet alleen die van gasten. Een test pint dat vast.
- De hostname komt uit de request en is dus door de beller te sturen. Hij dient
  uitsluitend als sleutel in de configuratie en belandt nooit in de URL; een
  vervalste Host-header kiest hooguit iets wat een beheerder al had ingesteld,
  en de tenantcontrole in `validate_jwt` staat er nog steeds achter.
- Een waarde die geen tenant-ID of bekend sleutelwoord is, wordt genegeerd en
  gelogd in plaats van in de URL gezet. Een typefout stuurt niemand naar een
  aanmeldpagina die niet bestaat.

Zonder de env-var verandert er niets: leeg = het gedrag van hiervoor.

Wat dit oplevert: de klant beheert zijn gasten zelf, via dezelfde groep als zijn
eigen mensen. Geen tenant erbij in `ENTRA_ALLOWED_TENANTS`, geen Key
Vault-bewerking per persoon, en — belangrijker — de conditional access van de
klant blijft op hem van toepassing. Bij de omgekeerde route (zijn thuistenant
onboarden) was hij buiten élk beleid van de klant gevallen.

---

## 2026-08-31 — Opvolging van de gezondheidscheck: een margefout, minder ruis, meer spoor

Wat er uit de gezondheidscheck van 31 augustus kwam. Een echte fout in
`get_margin_report`, en verder vooral: de server vertelt nu wat er misgaat in
plaats van dat het uit de omliggende requests moest worden afgeleid. Twee keer
bleek dat de reden van een weigering werd weggegooid — bij Entra's antwoord op
de tokenuitwisseling, en bij een geweigerd token in de middleware.

### `nightly-restart.yml` verwijderd

De workflow herstartte de productie-revisie elke nacht om 23:00 UTC. Hij stond er
als workaround voor iets wat niemand had onderzocht. Op 27-08 bleek de oorzaak:
een trage query blokkeerde de event loop, `/healthz` antwoordde niet, en Azure
schoot de replica af — 13x op een dag. Dat is opgelost door de tools via
`asyncio.to_thread` te draaien (punt #13).

Sindsdien staat er in `ContainerAppSystemLogs` geen enkele `ReplicaUnhealthy`
meer. De laatste is van 27-08 09:06, ruim voor de deploy van 18:48 die de fix
bracht. De nachtelijke restart herstartte dus alleen nog een gezonde container,
en verborg intussen precies het signaal dat je wilt zien als het weer misgaat.

### Azure-SDK logt niet meer elke header

De `HttpLoggingPolicy` van azure-core logt op INFO van elke call de volledige
request- en response-headers. `keyvault.get_secret` draait op een cache van vijf
minuten, dus dat gebeurde de hele dag door. Grofweg de helft van het logvolume
bestond eruit, en een echte fout stond ergens tussen twintig regels
`x-ms-keyvault-network-info`.

`build_app()` zet die loggers nu op `AZURE_SDK_LOG_LEVEL`, default `WARNING`.
Waarschuwingen en fouten van de SDK komen dus gewoon door. Moet je een
Azure-call uitpluizen, dan zet je de env-var op `INFO` of `DEBUG` — een
container-app-setting, geen redeploy.

### Een 400 van Entra noemt nu zijn eigen reden

Op 31-08 werden 17 `refresh_token`-grants geweigerd. De client krijgt dan een
401, logt opnieuw in en gaat verder — zelfherstellend, maar de gebruiker ziet
een herauthenticatie die er niet hoort te zijn.

Waarom Entra weigerde was niet te achterhalen. De log zei "Entra token endpoint
returned 400" en verder niets: het antwoord ging ongelezen door naar de client.
En zo'n weigering verschijnt *niet* in de Entra sign-in logs, ook niet onder
`nonInteractiveUser` — er was dus geen tweede plek om het na te kijken.

`_entra_error_summary()` logt nu het OAuth-foutenvelop: `error`, `error_codes`,
de eerste regel van de AADSTS-tekst en de `correlation_id` waar Microsoft support
om vraagt. Expliciet die velden, niet het hele antwoord: dan kan een veld dat
Entra er ooit bij zet niet ongemerkt meeliften. De oorzaak is hiermee nog niet
weg — de volgende keer noemt hij zichzelf.

### Een geweigerd token laat nu een spoor na

Zelfde blinde vlek, andere plek. `EntraAuthMiddleware` gaf bij een ongeldig
token een 401 terug zonder ook maar iets te loggen. Een gastgebruiker die van
Entra netjes een token kreeg maar bij elke call werd geweigerd, was daardoor
onzichtbaar: geen weigering in de log, geen sign-in bij ons te zien, alleen een
rij 401's. De oorzaak moest worden afgeleid uit de omliggende requests.

De middleware logt nu de reden uit `validate_jwt` — die noemt de waarde die niet
klopte (`Untrusted tenant: <tid>`, `Wrong audience: got ...`). Precies dat
onderscheidt een gast die op de verkeerde tenant inlogt van een verlopen token.
Met `%r` en een lengtelimiet, want die waarden komen uit het token zelf.

Het gewone tokenloze eerste verzoek — elke MCP-client haalt daarmee de
`WWW-Authenticate` op — logt bewust niets. Dat zijn er tientallen per uur en het
is geen fout; zou dat pad ook loggen, dan verdrinkt de weigering die er wel toe
doet. Een test bewaakt allebei.

### `get_margin_report` viel om op echte databasewaarden

Gevonden in de logs van 28-08: vier keer een `TypeError` bij een gebruiker die
om marge per land met vorig jaar erbij vroeg. Geen verkeerd getal maar helemaal
geen antwoord — de tool viel, de gebruiker kreeg een referentienummer.

pyodbc geeft `turnover` en `cost` terug als `Decimal`. `_enrich_margin_row`
rekende `margin` uit als float maar liet de rest via `**row` ongemoeid, dus een
rij droeg een `Decimal` omzet naast een float marge. In de gegroepeerde tak trok
`delta_turnover` vervolgens een float van een `Decimal` af, en dat weigert
Python.

De tak met `group_by="total"` viel niet om — daar staan aan beide kanten
`Decimal`s — maar had een stiller probleem: pydantic serialiseert een `Decimal`
als de *string* `"1000.50"`, dus het model kreeg omzet als tekst terwijl elke
andere tool een getal teruggeeft.

`_enrich_margin_row` normaliseert nu beide kolommen naar float, zoals de rest
van `tools.py` ze al behandelde. Eén plek in plaats van twee losse reparaties.

Waarom de tests dit nooit zagen: ze mockten `_fetch_margin` uitsluitend met
floats, waardoor het pad dat de echte database oplevert niet werd uitgevoerd.
Twee tests met `Decimal` dekken dat nu — en ze falen aantoonbaar zonder de fix.

### Verder

- `docs/ONBOARDING-CUSTOMER.md` en `docs/OPERATING-MODEL.md` zeiden nog "20
  tools"; dat zijn er 25 (punt #6 gesloten).
- Punt #22 in §11 voor de margefout hierboven; punt #20 voor de refresh-weigeringen, met wat er nu wel en
  niet over bekend is, en #21 voor gastgebruikers die op hun eigen thuistenant
  inloggen en daardoor op `Untrusted tenant` stranden.

---

## 2026-08-27 — `ruff` exact gepind, CI weer groen

De format-check faalde op `CHANGELOG.md`. Niet op de code: ruff formatteert ook
Python-codeblokken **in Markdown**, en het voorbeeld in de samenvatting hierboven
had uitgelijnde trailing comments die hij wil inkorten tot twee spaties.

De echte oorzaak is dat `ruff` ongepind stond (`>=0.4.0`). Lokaal stond 0.16.4,
CI installeerde 0.16.5, en die versie behandelt Markdown anders. Dan is de
format-check niet reproduceerbaar: groen op je eigen machine, rood in CI, zonder
dat er iets aan de code veranderd is.

Nu `ruff==0.16.5`, exact. Een linter mag meebewegen, een *formatter* niet:
zijn uitvoer is de norm waar de check op toetst. Bumpen kan gewoon, maar dan in
één commit met `ruff format .` over de hele repo — dat staat nu ook in
`CLAUDE.md`.

---

## 2026-08-27 — Ronde van 27 augustus (samenvatting)

Zes wijzigingen op één dag, hieronder elk apart beschreven. Dit blok vertelt wat
het samen betekent.

### Voor Roland: wat verandert er voor jou

**Doe eerst `git pull`.** Er staan tien commits op `main` van 27 augustus.

**1. Tools zijn nu `async`.** Dit is de enige wijziging die je werkwijze raakt.
Een nieuwe tool registreer je nog steeds in `server.py` met `@mcp.tool()` +
`_audited(...)`, maar het patroon is nu:

```python
@mcp.tool()
async def get_iets(start_date: str) -> dict:  # async def, niet def
    """..."""
    return await _audited(  # await
        "get_iets",
        {"start_date": start_date},
        lambda: _get_iets(start_date),  # de pure functie blijft synchroon
    )
```

De functie in `tools.py` blijft gewoon synchroon — die draait in een
worker-thread. Alleen de registratie in `server.py` is async geworden. Vergeet je
het `await`, dan krijg je een coroutine terug in plaats van een dict en faalt je
test meteen.

**2. Hang je nieuwe tool aan een rol.** In `roles.py` staat per rol welke tools
erbij horen. Zit je tool in geen enkele rol, dan faalt `tests/test_roles.py` —
met opzet, want een tool die niemand mag aanroepen is een configuratiefout.
Weet je het niet zeker: `_FINANCE_TOOLS` is meestal de juiste.

**3. Foutafhandeling loopt via `client_error()`.** Niet meer
`error=f"Execution error: {e}"`, maar:

```python
except Exception as e:
    logger.exception("get_iets failed")
    return format_response(data=[], error=client_error(e))
```

De volledige tekst gaat naar het log en de audit-entry; de klant krijgt een
neutrale zin met een referentie waarmee jij de fout terugvindt via
`GET /admin/audit/{referentie}`. Reden: pyodbc noemt de driver, de servernaam en
soms een stuk SQL, en dat belandde in het gespreksvenster van de klant.

**4. Vrije SQL cachet niet meer.** `execute_query(...)` heeft er een parameter
`use_cache` bij (default `True`). Zet hem op `False` als vers belangrijker is
dan snel.

**5. `;` in een query wordt geweigerd** behalve als afsluiter. Keyword-checks
kijken nu naar de query met stringliterals leeggemaakt, dus een product dat
"update kit" heet werkt eindelijk gewoon.

**6. De container draait Python 3.14**, en de tests op CI nog op 3.12. Houd daar
rekening mee als je iets versie-gevoeligs gebruikt.

**7. `CAMPAI_ALLOWED_GROUP` bestaat niet meer.** Toegang loopt volledig via de
allowlist in Key Vault.

Verder: 357 → 475 tests. `pytest -q` en `ruff check . && ruff format --check .`
moeten groen zijn vóór je pusht, CI blokkeert de deploy anders.

### Wat er is opgelost

**De disconnects.** Dit was de aanleiding voor de hele ronde. De MCP SDK roept
een synchrone tool-functie rechtstreeks op de event loop aan, dus tijdens een
query van maximaal 180 seconden antwoordde het proces niets — ook `/healthz`
niet. Azure las dat als een dode container en herstartte hem, waarna iedereen
die verbonden was eruit vloog. Dertien van die restarts op 27 augustus alleen
al, aantoonbaar uit `ContainerAppSystemLogs`. Sinds de fix: nul.

Verder in deze ronde: de OAuth-proxy was op ons eigen domein een open relay naar
Entra, rauwe databasefouten gingen naar de klant, de audit-configuratie stond
niet in de bicep (een `az deployment group create` had prod teruggezet naar een
SQLite-bestand in `/tmp`), de query-cache groeide onbegrensd, één gebruiker kon
alle acht databaseverbindingen bezetten, en databasetekst ging ongemarkeerd naar
het model.

### Wat er nog moet gebeuren

| Wat | Wie |
|---|---|
| Vijf app roles aanmaken en toewijzen in Entra (`docs/DEPLOYMENT.md`) | Campai |
| Daarna `ROLE_ENFORCEMENT=enforce` — nu staat het op `warn` en wordt er niets geweigerd | Campai |
| `saazuredb`-wachtwoord roteren (stond al open) | Campai + leverancier |
| `nightly-restart.yml` weghalen zodra `ReplicaUnhealthy` een week wegblijft | Campai |
| 3.14 aan de CI-matrix toevoegen, zodat het vinkje de runtime dekt | Campai |

---

## 2026-08-27 — `scripts/discover_schemas.py` gered

Op de laptop stond nog een losse kopie van dit project uit april, buiten git om.
Die is opgeruimd; dit script was het enige eruit dat het bewaren waard was.

Het dumpt de kolomnamen van ADMCMS-tabellen via de `get_schema`-tool, precies de
gewoonte die `CLAUDE.md` voorschrijft: verifiëren in plaats van gokken. ADMCMS
straft gokken af — `Quantity` heet `ThisIinvoice`, `UnitPrice` heet
`SalesPrice`, en `CustSuppnr` op Salesinvoice heet `Customer`.

Bijgewerkt ten opzichte van de oude versie: een `--token` (of `ADMCMS_MCP_TOKEN`)
zodat hij ook tegen de echte server werkt en niet alleen tegen een lokale met
`AUTH_DISABLED=true`, en `--tables` om andere tabellen op te vragen.

---

## 2026-08-27 — Rollen per tool

Tot nu toe kende de server één soort gebruiker: wie op de allowlist stond mocht
alles. Een financieel manager kon vrije SQL draaien en een IT-manager kon de
marges opvragen — niet omdat dat de bedoeling was, maar omdat er geen onderscheid
bestond.

Vijf rollen, in [`roles.py`](src/admcms_mcp/roles.py):

| Rol | Krijgt bovenop de basis |
|---|---|
| `finance` | omzet, marge, KPI's, klanten, kosten, kortingen, betaalmethoden, inkoop |
| `sales` | omzet, top-klanten, orders, voorraad, kanaalmix, salesrep |
| `it` | `query_database`, `detect_price_anomalies`, voorraad, orders |
| `viewer` | voorraad |
| `admin` | alles |

`describe_server`, `health_check`, `get_schema`, `get_lookups`,
`search_products` en `get_filter_options` zitten in elke rol: drie beschrijven de
server in plaats van de business, de andere drie heb je nodig voordat je een
zinnige vraag kunt stellen. Meerdere rollen tellen op.

**Waar de rol vandaan komt.** Uit de `roles`-claim van het token, gevuld door de
app roles in de app-registratie. Bewust niet uit groepslidmaatschap: boven ~200
groepen laat Entra de `groups`-claim weg en zou iemand stilzwijgend zijn rol
kwijtraken. `admcms.invoke` staat in dezelfde claim maar is geen rol en wordt
genegeerd.

**Twee plekken handhaven.** `tools/list` verbergt wat je niet mag — en
`describe_server` volgt vanzelf, want dat leest dezelfde lijst — en `_audited`
weigert de aanroep zelf, met de weigering in het audit-log. Verbergen is geen
autorisatie; de tweede check is dat wel.

**Staat op `warn`.** Er wordt nog niets geweigerd, alleen gelogd. Dat is met
opzet: eerst de app roles toewijzen in Entra, dan de logs lezen, dan
`ROLE_ENFORCEMENT=enforce`. Andersom sluit één vergeten toewijzing iedereen
tegelijk buiten. De stappen staan in `docs/DEPLOYMENT.md`.

`/admin/*` valt hier niet onder; dat blijft op de `admins`-sectie van de
allowlist lopen. Eén mechanisme per beslissing.

`describe_server` vertelt de gebruiker nu ook welke rollen hij heeft — zonder dat
is "die tool bestaat niet" niet te onderscheiden van "die tool is niet voor jou".

Tests: 32 nieuwe, waaronder twee die de tabel eerlijk houden — elke tool in een
rol moet bestaan, en elke geregistreerde tool moet in minstens één rol zitten.
Vergeet je dat bij een nieuwe tool, dan faalt de suite.

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
| SQL-auditing bewaart 7 dagen — kort als je wilt weten of het gelekte wachtwoord gebruikt is | Campai |
| Vijf app roles aanmaken en toewijzen in Entra, daarna `ROLE_ENFORCEMENT=enforce` | Campai |
| `nightly-restart.yml` weghalen zodra `ReplicaUnhealthy` een week wegblijft | Campai |
| Python 3.14 aan de CI-matrix toevoegen — de container draait er al op, de tests nog op 3.12 | Campai |

Afgehandeld op 27-08-2026: de oorzaak van de nachtelijke restart is gevonden en
opgelost (zie de ronde van 27 augustus), en `health_check.tool_count` telt nu de
tools in plaats van een hardcoded 23.
