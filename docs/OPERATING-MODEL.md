# Operating model — wie doet wat

## Drie partijen

| Partij | Identiteit | Rol |
|---|---|---|
| **Campai (MSP)** | Tenant `bf030cec-...` (`campai.nl`) | Bouwt, deployt, host, beheert |
| **CMS Lelystad (klant)** | Tenant `a92e7c27-...`, subscription `CMS-MCP-PAYG` | Betaalt subscription, beheert eigen gebruikers |
| **Roland (Campai dev)** | Campai user | Tool-functionaliteit, SQL-queries, lokaal testen |

## Eigendom per resource

| Resource | Eigenaar | Beheerd door |
|---|---|---|
| Subscription `CMS-MCP-PAYG` | CMS Lelystad (betaalt) | Campai (Owner/Contributor) |
| Resource group `CMS-MCP-rg-prod` | Campai | Campai daily ops |
| Container App + ACR + KV + MI | Campai | Campai daily ops |
| Entra app `CMS-MCP-API` | Campai tenant | Campai GA voor wijzigingen, daily voor monitoring |
| GitHub repo `AzureMCP-CMS` | Campai | Campai dev team |
| ADMCMS database `cmsnl` | CMS (data) + Campai (technisch) | Bestaand, niet door dit project beheerd |
| Security group `SG-CMS-MCP-Users` (`68f3ad86-...`) | CMS tenant | CMS IT-admin |
| Security group `SG-Campai-ADMCMS-Users` (`bd6ce8cc-...`) | Campai tenant | Owners: Roland + Janneke |

## Verantwoordelijkheden per partij

### Campai (MSP)

**Dagelijkse ops** (Janneke@campai.nl als primaire contactpersoon):
- Monitoren van de Container App (Application Insights `CMS-MCP-ai-prod`)
- Restart / scale-up bij problemen
- Key Vault secrets roteren wanneer nodig (DB password)
- Nieuwe Campai-collega's toevoegen aan `SG-Campai-ADMCMS-Users` group (geen KV-touch)
- Nieuwe CMS-groups toevoegen aan KV allowlist na onboarding (zelden, ~1× per klant)
- PR-reviews + merges naar `main` (triggert auto-deploy)
- Incident response

**Roland** (als Owner van `SG-Campai-ADMCMS-Users`):
- Kan zelfstandig Campai-collega's toevoegen/verwijderen uit de group
- Voor toegang tot ADMCMS data via Claude/Copilot/Cursor

**Eenmalig / occasioneel** (vereist GA / break-glass account):
- App registratie aanpassen (scopes, redirect URIs, owners)
- Nieuwe tenant toevoegen aan `ENTRA_ALLOWED_TENANTS`
- Bicep-wijzigingen die role assignments raken
- Federated OIDC credentials uitbreiden (nieuwe branches/repos)

### CMS Lelystad (klant)

**CMS IT-admin**:
- Eenmalig: admin-consent geven (`/adminconsent?client_id=...` URL — zie [`ONBOARDING-CUSTOMER.md`](ONBOARDING-CUSTOMER.md))
- Eenmalig: security group `CMS-MCP-Users` maken en object ID doorgeven
- Doorlopend: gebruikers toevoegen/verwijderen uit de security group
- Op verzoek van Campai: ondersteuning bij troubleshooting (token claims, group membership)

**CMS eindgebruikers**:
- Claude/Copilot/Cursor configureren (zie [`CLIENT-SETUP.md`](CLIENT-SETUP.md))
- Hun werk doen — vragen stellen aan de AI

**CMS subscription billing**:
- Betalen de Azure-kosten van `CMS-MCP-PAYG` direct aan Microsoft
- Indicatieve maandelijkse kosten Container Apps + ACR + KV + Log Analytics:
  ~€30-80/maand bij laag-tot-medium gebruik (scale-to-zero is uit, min 1 replica)

### Roland (developer)

- Tool-functionaliteit bouwen in `src/admcms_mcp/tools.py`
- Lokaal testen met `AUTH_DISABLED=true` of tegen live (zie [`TESTING.md`](TESTING.md))
- Tests schrijven in `tests/`
- PRs openen tegen `main` — bij merge auto-deploy via GitHub Actions
- Bij DB-schema kennis: bijwerken van `docs/DATABASE-KENNIS/`

## Wat doet Campai NIET

- We kijken niet mee in CMS-gebruikersdata. Audit log gaat over wie wat
  bevraagde, niet over inhoud van resultaten (resultaten worden gestreamd naar
  de gebruiker, niet opgeslagen op onze server buiten transient memory).
- We managen geen CMS-gebruikersaccounts (dat doet CMS IT-admin in hun tenant).
- We schrijven niet naar ADMCMS — alle 25 tools zijn SELECT-only.

## Tweetraps escalatie

| Niveau | Wie | Wat |
|---|---|---|
| L1 (gebruiker probleem) | CMS IT-admin | "Mijn Claude geeft 403" → check group membership |
| L2 (configuratie / infra) | Campai daily (Janneke) | "Niemand kan in" → logs, KV secrets, env vars, restart |
| L3 (Entra / app reg) | Campai GA / break-glass | "App reg moet aangepast" → permissions, redirect URIs |

## Wijzigingen aan dit operating model

Wijziging documenteren in dit bestand + commit + PR. Bij grote wijziging
(bv. een tweede klant erbij, V2 features): nieuwe design spec onder
`docs/superpowers/specs/`.
