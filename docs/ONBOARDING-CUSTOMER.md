# Klant-onboarding — CMS Lelystad

Procedure om CMS-tenant toegang te geven tot de MCP-server. Onderdelen 1 en 2
zijn voor de **CMS IT-admin**, onderdeel 3 voor **Campai** (zodra CMS klaar is).

## Wat is dit?

De ADMCMS MCP-server draait in Campai's Azure tenant, maar de **Entra app
registratie is multi-tenant**: CMS-gebruikers loggen in met hun eigen
CMS-account in hun eigen tenant. Geen gastaccounts in Campai nodig.

Toegang loopt via twee **parallelle security groups** — één per tenant:
- `SG-Campai-ADMCMS-Users` in Campai tenant (voor Campai dev/support)
- `SG-CMS-MCP-Users` in CMS tenant (voor jullie eindgebruikers — onderstaande stappen)

Beide groups staan onafhankelijk op de allowlist. Elke partij beheert zijn eigen
group. Geen kruisverkeer.

| Item | Waarde |
|---|---|
| MCP endpoint | `https://cms-mcp-app-prod.redcoast-c9b157ab.westeurope.azurecontainerapps.io/mcp` |
| Entra App ID | `1e6983d3-f996-4581-9518-e6819b79ac6a` |
| App display name | `CMS-MCP-API` |
| Scope | `admcms.read` |
| Home tenant (Campai) | `bf030cec-d527-4f3c-9843-db5473bee3f7` |
| CMS Lelystad tenant | `a92e7c27-7bb0-4bc3-aea5-89df54c243ad` |

---

## Stap 1 — CMS IT-admin: Admin-consent geven (~ 2 minuten)

CMS IT-admin (iemand met `Global Administrator` of `Privileged Role Administrator`
in CMS tenant) opent in een browser:

```
https://login.microsoftonline.com/a92e7c27-7bb0-4bc3-aea5-89df54c243ad/adminconsent?client_id=1e6983d3-f996-4581-9518-e6819b79ac6a
```

Stappen:

1. Log in met een CMS Global Admin account
2. Microsoft toont: **"CMS-MCP-API wants permission to:"**
   - Sign you in and read your profile (Microsoft Graph — User.Read)
   - Read ADMCMS data on behalf of user (`admcms.read`)
3. Klik **Accept**
4. Je krijgt mogelijk een "wrong page" of een lege pagina te zien — dat is
   cosmetisch, consent is geslaagd

Verifieer in Azure Portal → **CMS tenant** → Entra ID → Enterprise applications →
zoek `CMS-MCP-API`. Hij moet daar staan als type `Application`.

## Stap 2 — CMS IT-admin: Security group voor toegang

Maak in CMS tenant een security group, bijvoorbeeld `CMS-MCP-Users`:

1. Azure Portal → Entra ID → Groups → New group
2. Type: Security
3. Name: `CMS-MCP-Users`
4. Description: "Toegang tot de ADMCMS MCP server bij Campai"
5. Voeg gebruikers toe die de MCP mogen gebruiken
6. Na aanmaken: kopieer het **Object ID** van de group

Stuur dat **Group Object ID** door naar Campai (Janneke@campai.nl).

> Alternatief: lever een lijst van individuele user OIDs aan in plaats van een
> groep. Voor onboarding meer dan ~5 gebruikers is groep aanbevolen.

## Stap 3 — Campai: toegang activeren

Campai voert eenmalig uit (vanaf daily account, na ontvangen group OID):

```powershell
az login
az account set --subscription 1886dd8f-1ce7-4af2-8491-338341544b55

# Voeg CMS group OID toe aan allowlist
$current = az keyvault secret show --vault-name cms-mcp-kv-prod --name admcms-allowlist --query value -o tsv | ConvertFrom-Json
$current.groups = @($current.groups) + "<CMS_GROUP_OID>"
$updated = $current | ConvertTo-Json -Compress
az keyvault secret set --vault-name cms-mcp-kv-prod --name admcms-allowlist --value $updated

# Restart Container App zodat hij de nieuwe allowlist ophaalt (cache 5 min anders)
$rev = az containerapp revision list -n cms-mcp-app-prod -g CMS-MCP-rg-prod --query "[?properties.active].name | [0]" -o tsv
az containerapp revision restart -n cms-mcp-app-prod -g CMS-MCP-rg-prod --revision $rev
```

Vanaf nu kunnen alle CMS-gebruikers in de security group de MCP gebruiken.

## Stap 4 — CMS eindgebruiker: gebruik in Claude/Copilot

Zie [`CLIENT-SETUP.md`](CLIENT-SETUP.md) voor configuratie per AI-tool.

---

## Toegang intrekken

| Scope | Hoe |
|---|---|
| **Eén gebruiker** | CMS IT-admin verwijdert uit security group `CMS-MCP-Users` (max 5 min cache) |
| **Tijdelijk alle CMS-toegang pauzeren** | Campai verwijdert CMS tenant ID uit `ENTRA_ALLOWED_TENANTS` env var op Container App |
| **Definitief afsluiten** | CMS IT-admin verwijdert de enterprise app `CMS-MCP-API` uit Entra → Enterprise applications |

## Wat ziet CMS van de data?

- **Lezen, niet schrijven**: alle 20 tools zijn SELECT-only, met SQL identifier
  validation en parameterized queries. De server weigert non-SELECT.
- **Audit log**: elke tool-call wordt geregistreerd met `upn`, `oid`, `tid`
  (tenant), duur, gestelde queries. Campai kan deze loggen inzien voor compliance.
- **Geen credentials in de container**: DB password staat in Campai's Key Vault,
  Container App haalt het op via Managed Identity bij start.

## Vragen / problemen

Contact: Janneke Post (`Janneke@campai.nl`)

## Grote tenants: de `groups`-claim kan wegvallen

Zit een gebruiker in meer dan ongeveer 200 Entra-groepen, dan laat Entra de
`groups`-claim uit het token weg en zet er een verwijzing (`_claim_names` /
`hasgroups`) voor in de plaats. De MCP haalt het lidmaatschap niet alsnog bij
Graph op, dus voor zo iemand telt alleen de **users**-lijst van de allowlist.

Symptoom: één gebruiker krijgt "User not on allowlist" terwijl collega's in
dezelfde groep gewoon binnenkomen. De weigering zegt dat er sprake is van een
overage; in de containerlogs staat dan ook een waarschuwing met de UPN.

Oplossing: zet die persoon met zijn `oid` in de `users`-lijst van het
`admcms-allowlist`-secret. Datzelfde geldt voor de `admins`-sectie.
