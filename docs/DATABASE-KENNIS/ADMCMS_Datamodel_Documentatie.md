# ADMCMS Database — Volledig Datamodel & Documentatie

**Versie:** 1.0
**Datum:** 6 april 2026
**Database:** ADMCMS-PRODUCTION-REPL01 op cmsnl.database.windows.net
**Opgesteld door:** Campai (Roland Slagter)
**Doel:** Referentiedocument voor het bevragen van de ADMCMS legacy database

---

## 1. Systeemoverzicht

### Wat is ADMCMS?

ADMCMS is een legacy ERP/distributiesysteem voor de **automotive aftermarket** — het beheert de volledige supply chain van auto-onderdelen: inkoop bij fabrikanten, opslag in warehouses, verkoop aan dealers/klanten, en uitlevering via diverse carriers.

### Kerncijfers

| Gegeven | Waarde |
|---------|--------|
| Totaal tabellen | 45 |
| Totaal views | 1 |
| Totaal stored procedures | 135 |
| Totaal rijen | 82.265.895 |
| Gedefinieerde foreign keys | 0 (alle relaties zijn impliciet) |
| Historische data vanaf | 2004 |
| Primaire valuta | EUR |
| Primaire warehouse | CM (Central Warehouse) |

### Bedrijfscontext

- **Branche:** Automotive aftermarket parts (motoronderdelen, OEM replacements)
- **Klanten:** Dealers en winkels, voornamelijk in Europa (NL, DE, FR, IT, UK) en Japan
- **Leveranciers:** Fabrikanten in EUR, USD, GBP, JPY, THB zones
- **Betaalmethodes:** Voornamelijk prepaid, creditcard (Adyen: iDeal, PayPal, Visa, Mastercard), en op rekening
- **Carriers:** FedEx, UPS, DHL

---

## 2. Kritieke technische conventies

### 2.1 Datumopslag

**ALLE datums** zijn opgeslagen als `char(8)` in **YYYYMMDD** formaat.

| Voorbeeld | Betekenis |
|-----------|-----------|
| `'20260406'` | 6 april 2026 |
| `'00000000'` | Leeg / onbekend / niet ingevuld |
| `'99999999'` | Onbepaald / geen einddatum |

**SQL patronen voor datums:**

```sql
-- Filter op jaar 2025
WHERE InvoiceDate >= '20250101' AND InvoiceDate <= '20251231'

-- Filter op Q1 2026
WHERE InvoiceDate >= '20260101' AND InvoiceDate <= '20260331'

-- Filter "vandaag" (dynamisch)
WHERE InvoiceDate = FORMAT(GETDATE(), 'yyyyMMdd')

-- Uitsluiten van lege datums
WHERE InvoiceDate > '00000000' AND InvoiceDate < '99999999'

-- Converteren naar leesbare datum
SELECT CONVERT(date, InvoiceDate, 112) AS InvoiceDateFormatted

-- Datumvelden extraheren
SELECT LEFT(InvoiceDate, 4) AS Jaar,
       SUBSTRING(InvoiceDate, 5, 2) AS Maand,
       RIGHT(InvoiceDate, 2) AS Dag
```

### 2.2 Tijdopslag

Tijden zijn opgeslagen als `int` — **seconden sinds middernacht**.

| Waarde | Tijd |
|--------|------|
| 0 | 00:00:00 |
| 37029 | 10:17:09 |
| 43200 | 12:00:00 |
| 86399 | 23:59:59 |

```sql
-- Converteren naar uren:minuten
SELECT 
    Time_ / 3600 AS Uren,
    (Time_ % 3600) / 60 AS Minuten,
    Time_ % 60 AS Seconden
```

### 2.3 Financiële periodes

`FinPeriod` is opgeslagen als `int` in **YYYYMM** formaat.

| Waarde | Betekenis |
|--------|-----------|
| 202604 | April 2026 |
| 202501 | Januari 2025 |

```sql
-- Filter op boekjaar 2025
WHERE FinPeriod >= 202501 AND FinPeriod <= 202512

-- Huidige periode
WHERE FinPeriod = CAST(FORMAT(GETDATE(), 'yyyyMM') AS INT)
```

### 2.4 Impliciete relaties (geen foreign keys)

De database heeft **nul formele foreign keys** gedefinieerd. Alle relaties zijn impliciet via naamgeving. Dit is de complete relatie-mapping:

```
CustSupplier.CustSuppnr ──→ Salesinvoice.Customer
                          ──→ SalesOrderHeader.Customer
                          ──→ PaymentTrxLog.Customer
                          ──→ PurchaseOrder.Supplier (waar CustSuppType='2')

CustSupplier.InvoiceAddressID ──→ Addresses.AddressID
CustSupplier.DefdeliveryAddressID ──→ Addresses.AddressID

Addresses.AddressID ──→ AddressContacts.AddressID
                    ──→ AddressRemarks.AddressID

Product.Productcode ──→ SalesinvoiceLines.Productcode
                    ──→ SalesOrderLines.Productcode
                    ──→ ProductWarehouse.Productcode
                    ──→ ProductSupplier.Product
                    ──→ ProductTurnover.Product
                    ──→ StockTransactions.ProductID
                    ──→ PurchaseOrderLines.Product
                    ──→ BulkbatchChecks.Product
                    ──→ Bulkbatchlines.ProductID
                    ──→ BulkReceiptsDetail.ProductID
                    ──→ PriceProdCustgrp.Product

Salesinvoice.Invoicenr ──→ SalesinvoiceLines.Invoicenr
                       ──→ Cartons.Invoice
                       ──→ PaymentTrxLog.Invoice

SalesOrderHeader.Ordernr ──→ SalesOrderLines.Ordernr

PurchaseOrder.Ordernr ──→ PurchaseOrderLines.Ordernr
                      ──→ PurchaseOrderlineDetail.Ordernr

PurchaseOrderInvoice.Invoice ──→ PurchaseOrderInvoiceLines.Invoice
                             ──→ PurchaseOrderInvoices.Invoice

BulkbatchHeader.BatchID ──→ Bulkbatchlines.BatchID
                        ──→ BulkbatchChecks.BatchID
                        ──→ BulkbatchPerformance.BatchID

BulkReceipts.ReceiptID ──→ BulkReceiptsDetail.ReceiptID

Receipts.ReceiptID ──→ ReceiptsDetail.ReceiptID

Warehouse.Code ──→ ProductWarehouse.Warehouse
               ──→ StockTransactions.Warehouse
               ──→ SalesinvoiceLines.Warehouse
               ──→ PurchaseOrder.Warehouse

Employees.Code ──→ Salesinvoice.PickedBy / PackedBy / PreppedBy / CheckedBy
               ──→ PickingList.PickedBy / PackedBy / PreppedBy / CheckedBy
               ──→ StockTransactions.Employee / EmployeeBooking
               ──→ BulkbatchPerformance.ByEmployee
               ──→ Receipts.EmployeeCreated

AdyenPaymentMethods.PaymentMethod ──→ PaymentTrxLog.AdyenPaymentMethod
```

---

## 3. Domeinmodel

### 3.1 Klanten & Adressen

#### CustSupplier — Klant/leverancier master (478.673 rijen)

De centrale tabel voor alle klanten en leveranciers. Het veld `CustSuppType` bepaalt het type.

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| CustSuppnr | int | **PK** (samen met CustSuppType) — Uniek nummer | |
| CustSuppType | varchar(1) | **PK** — Type | `1` = klant (477.185), `2` = leverancier (1.488) |
| pActive | bit | Actief? | True (478.151), False (522) |
| CustSuppGroup | varchar(4) | Klantengroep | `ND` = standaard (472.499), `G2` (4.481), `JBL` (407), `POA` (213), + 20 andere |
| InvoiceAddressID | int | → Addresses.AddressID | |
| DefdeliveryAddressID | int | → Addresses.AddressID | |
| Currency | varchar(3) | Standaard valuta | `EUR` (478.569), `USD` (46), `GBP` (25), `JPY` (14) |
| Accountinggroup | varchar(4) | Boekhoudgroep | `1300` (476.957), `1600` (837), `1610` (653) |
| PaymentCondition | varchar(4) | Betaalconditie | `PREP` prepaid (420.649), `CC` creditcard (44.392), `AC` op rekening (8.650), `LINK` paylink (2.900), `ACC` (793) |
| DeliveryCondition | varchar(3) | Leveringsconditie | ` ` (477.856), `EXW` (588), `SD` (198), `FRA` (17) |
| Creditlimit | int | Kredietlimiet in EUR | |
| OpenAmount | decimal | Openstaand bedrag | |
| VATNumber | varchar(15) | BTW-nummer | |
| PaymentProblem | bit | Betalingsprobleem? | False (477.707), True (966) |
| RedListed | bit | Rode lijst? | False (478.219), True (454) |
| OnHoldReason | smallint | Reden van hold | 0=geen (477.892), 1-8 diverse redenen |
| C_NU_DeliveryStrategy | varchar(1) | Leveringsstrategie | `1` standaard (477.346), `2`-`5` |
| C_NU_DeliveryDays | varchar(5) | Leverdagen (Ma-Vr flags) | ` ` (393.376), `NNNNN` (84.589), `YNNNN` alleen maandag, etc. |
| WebAccountType | tinyint | Type webaccount | 1 (467.418), 0 (10.757), 5 (498) |
| InvoicingOrderingType | varchar(1) | Type facturering | `2` (475.042), `1` (3.566) |
| Intercompany | smallint | Intercompany vlag | 0 (478.673) |
| CreationDate | char(8) | Aanmaakdatum YYYYMMDD | |

#### Addresses — Adressen (573.690 rijen)

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| AddressID | int | **PK** | |
| Active | bit | Actief? | True (573.668), False (22) |
| Type | varchar(1) | Adrestype | `1` = hoofd/factuur (11.782), `2` = aflever (561.908) |
| FullName | nvarchar(64) | Volledige naam | |
| PostalAddress | nvarchar(64) | Postadres | |
| ZipPA | varchar(12) | Postcode postadres | |
| CityPA | nvarchar(64) | Stad postadres | |
| Country | varchar(2) | ISO landcode | 152 landen |
| Language | varchar(3) | Taal | `E` Engels (334.309), `NL` (77.812), `D` Duits (66.876), `F` Frans (65.935), `IT` (23.276), `JP` (3.886) |
| AR | bit | Debiteur? | True (485.481), False (88.209) |
| AP | bit | Crediteur? | False (572.133), True (1.557) |
| OnHold | bit | On hold? | False (572.765), True (925) |
| Email | varchar(255) | E-mailadres | |

#### AddressContacts — Contactpersonen (565.455 rijen)

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| AddressID | int | **PK** → Addresses | |
| ContactID | int | **PK** — Volgnummer | |
| Firstname | nvarchar(30) | Voornaam | |
| Lastname | nvarchar(60) | Achternaam | |
| Gender | varchar(1) | Geslacht | `1` man (424.044), `2` vrouw (13.219), `3` onbekend (128.192) |
| Email | varchar(256) | E-mailadres | |
| Phone / MobilePhone / WorkPhone | varchar(15) | Telefoonnummers | |

#### AddressRemarks — Opmerkingen (105.802 rijen)

| Kolom | Type | Beschrijving |
|-------|------|-------------|
| AddressID | int | **PK** → Addresses |
| Line | int | **PK** — Regelnummer |
| DateEntered | char(8) | Invoerdatum YYYYMMDD (bereik: 2004-2026) |
| EnteredBy | varchar(4) | → Employees.Code |
| Text | varchar(max) | Opmerkingtekst |

---

### 3.2 Producten & Prijzen

#### Product — Productmaster (5.201.908 rijen)

De kern van het systeem — 5.2 miljoen auto-onderdelen.

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| Productcode | varchar(15) | **PK** — Productcode (bijv. '37250MBZD01') | |
| Type | varchar(7) | Producttype | |
| Code | varchar(7) | Korte code | |
| Model | varchar(15) | Model referentie | |
| Make | varchar(2) | Fabrikantcode | |
| ManufacturersCode | varchar(20) | Fabrikant artikelcode | |
| Productgroup | varchar(3) | Productgroep (3 tekens) | |
| Description | varchar(64) | Omschrijving | |
| DefaultSupplier | int | → CustSupplier.CustSuppnr (type 2) | |
| Unit | varchar(5) | Eenheid | `pcs` (2.741.519), `pce` (1.603.292), `stk` (856.882) |
| WeightInGrams | int | Gewicht in grammen | |
| Cost | decimal(18,2) | Kostprijs | |
| Salesprice | decimal(18,2) | Verkoopprijs | |
| SpecialPrice | decimal(18,2) | Actieprijs | |
| MaxDiscount | decimal(18,1) | Maximum korting % | |
| AvgPurchasePrice | decimal(16,4) | Gemiddelde inkoopprijs | |
| Availability | varchar(1) | Beschikbaarheid | `D` beschikbaar (3.222.227), `G` (1.967.252), ` ` (12.266) |
| InActiveProgram | bit | In actief assortiment? | True (5.182.680), False (19.228) |
| FreezeProduct | bit | Bevroren? | False (5.182.196), True (19.712) |
| HotItem | bit | Hot item? | False (5.174.754), True (27.154) |
| KIT | bit | Kit/bundel? | False (5.200.776), True (1.132) |
| Salesproductcode | varchar(1) | Verkoopproductcode | `M` (2.165.512), ` ` (1.329.066), `A` (1.059.594), `O` (231.835), `P`, `D`, `J`, `S`, `B`, `G`, `C`, `H` |
| Region | varchar(1) | Regio | `0` (3.453.514), `1` (1.534.537), `2` (213.857) |
| Discountgroup | varchar(6) | Kortingsgroep | ` ` (5.109.638), `CMS01` (46.315), `CMS05` (32.195), `CMS10`-`CMS55` |
| MayContainAsbestos | bit | Mogelijk asbest? | False (5.194.910), True (6.998) |
| OEcode | int | OE code type | 0 (5.188.114), 2 (11.959), 4 (1.427) |
| Length / Width / Height | decimal(5,2) | Afmetingen | |
| CreationDate | char(8) | Aanmaakdatum YYYYMMDD | |
| LastModificationDate | char(8) | Laatste wijziging YYYYMMDD | |

#### ProductSupplier — Product-leverancier koppeling (16.639.898 rijen)

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| Product | varchar(15) | **PK** → Product.Productcode | |
| Supplier | int | **PK** → CustSupplier.CustSuppnr (type 2) | |
| Active | bit | Actief? | True (16.575.733), False (64.165) |
| SupplierProductCode | varchar(25) | Leverancier artikelcode | |
| SupplierProductDesc | varchar(64) | Leverancier omschrijving | |
| Currency | varchar(3) | Inkoopvaluta | `EUR` (13.916.640), `USD` (1.448.230), `GBP` (660.986), `JPY` (405.156) |
| Purchaseprice | decimal(18,2) | Inkoopprijs | |
| PurchaseDiscount | decimal(18,1) | Inkoopkorting % | |
| LastKnownStock | int | Laatst bekende voorraad bij leverancier | |
| MinimumOrderQty | int | Minimum bestelhoeveelheid | |

#### ProductWarehouse — Voorraad per warehouse (1.003.075 rijen)

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| Productcode | varchar(15) | **PK** → Product | |
| Warehouse | varchar(2) | **PK** → Warehouse.Code | `CM` (525.224), `DS` (310.856), `IN` (132.742), `OS` (26.754) |
| ActualStock | int | Huidige voorraad | |
| ActualStockValue | decimal(18,2) | Voorraadwaarde | |
| CostPrice | decimal(16,4) | Kostprijs | |
| MinimumStock | int | Minimumvoorraad | |
| MaximumStock | int | Maximumvoorraad | |

#### ProductTurnover — Omzet per product per periode (6.022.395 rijen)

| Kolom | Type | Beschrijving |
|-------|------|-------------|
| Product | varchar(15) | **PK** → Product |
| FinPeriod | int | **PK** — Financiële periode YYYYMM |
| NumberOfPieces | int | Aantal verkocht |
| Turnover | decimal(18,2) | Omzet |
| Cost | decimal(18,2) | Kosten |
| NoSales | int | Aantal verkooptransacties |

#### Prijstabellen

**PriceProdCustgrp** (9.027 rijen) — Productprijs per klantengroep:
- PK: Product + Customergroup
- 24 klantengroepen: G0, G1, G2, G3, G4, G5, G6, G10, G15, G20, G25, G30, G32, G32A, G35, G42, G42A, G42B, G48, G50, ND

**PriceProdgrpCustgrp** (3.461 rijen) — Productgroepprijs per klantengroep

**ProdgrCustgrpSuppliers** (5.068 rijen) — Leverancierstoewijzing per productgroep/klantengroep

---

### 3.3 Verkoop

#### SalesOrderHeader — Verkooporders (6.915 rijen)

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| Ordernr | int | **PK** | |
| Customer | int | → CustSupplier.CustSuppnr | |
| OrderDate | char(8) | Orderdatum YYYYMMDD | |
| DeliveryDate | char(8) | Gewenste leverdatum YYYYMMDD | |
| OrderOrigin | varchar(4) | Herkomst | `12` (3.619), `06` (1.933), `11` (1.271) |
| Currency | varchar(3) | Valuta | `EUR` (alle) |
| Warehouse | varchar(2) | Warehouse | `CM` (alle) |
| PaymentCondition | varchar(4) | Betaalconditie | `PREP` (3.678), `AC` (2.151), `LINK` (855), `ACC` (212) |
| PreferredShipment | varchar(4) | Voorkeur carrier | ` ` (4.708), `UPSS` (609), `FEDE` (516), `DHFY` (391) |
| DropShipment | bit | Dropshipment? | False (5.649), True (1.271) |
| PrepaymentReceived | bit | Vooruitbetaling ontvangen? | True (3.656), False (3.264) |
| SalesOrdersystemStatus | varchar(1) | Status | `0` open (6.862), `3` (58) |
| OrderAmount | decimal(18,2) | Orderbedrag | |

#### SalesOrderLines — Orderregels (27.906 rijen)

| Kolom | Type | Beschrijving |
|-------|------|-------------|
| Ordernr | int | **PK** → SalesOrderHeader |
| Linenr | int | **PK** — Regelnummer |
| Productcode | varchar(15) | → Product |
| Description | varchar(64) | Omschrijving |
| SalesPrice | decimal(18,2) | Verkoopprijs |
| Discount1 / Discount2 | decimal(18,1) | Korting % |
| OpenOrder | int | Open hoeveelheid |
| Invoiced | int | Gefactureerd |
| ToDeliverFromStock | int | Te leveren uit voorraad |
| ToPurchase | int | In te kopen |
| CancelledByCustomer | bit | Geannuleerd door klant? |
| CancelReason | varchar(4) | Annuleringsreden |

#### Salesinvoice — Verkoopfacturen (1.610.863 rijen)

De grootste transactietabel. Bevat alle verkoopfacturen vanaf 2004.

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| Invoicenr | int | **PK** | |
| InvoiceType | varchar(1) | Type | `0` = normaal (1.585.012), `1` = creditnota (25.851) |
| Customer | int | → CustSupplier.CustSuppnr | |
| InvoiceAddress | int | → Addresses.AddressID | |
| DeliveryAddress | int | → Addresses.AddressID | |
| InvoiceDate | char(8) | Factuurdatum YYYYMMDD | |
| Shipdate | char(8) | Verzenddatum YYYYMMDD | |
| FinPeriod | int | Financiële periode YYYYMM | |
| Salesrep | varchar(4) | Verkoopmedewerker → Employees | |
| Currency | varchar(3) | Valuta | `EUR` (1.610.859) |
| PaymentCondition | varchar(4) | Betaalconditie | `PREP` (1.037.250), `AC` (266.742), `CC` (239.240), `ACD` (28.518), `ACC` (16.301) |
| ShipmentMethod | varchar(4) | Carrier | `FEDE`, `UPSS`, `DHLP`, etc. |
| LineTotal | decimal(18,2) | Regeltotaal | |
| VATTotal | decimal(18,2) | BTW totaal | |
| ShipCost | decimal(18,2) | Verzendkosten | |
| AmountHC | decimal(18,2) | Bedrag in thuisvaluta (EUR) | |
| FullyPaid | bit | Volledig betaald? | True (1.610.122), False (741) |
| Processed | bit | Verwerkt? | True (1.610.751), False (112) |
| Printed | bit | Geprint? | True (1.610.848), False (15) |
| PickedBy / PackedBy / PreppedBy / CheckedBy | varchar(4) | → Employees | |
| DeliveryCountry | varchar(2) | Land van levering | |
| ReceivedByAPICustomer | bit | Via API ontvangen? | False (1.493.378), True (117.485) |

#### SalesinvoiceLines — Factuurregels (12.608.071 rijen)

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| Invoicenr | int | **PK** → Salesinvoice | |
| Ordernumber | int | **PK** — Ordernummer | |
| Orderline | int | **PK** — Orderregel | |
| MasterOrderline | int | **PK** — Master orderregel | |
| Productcode | varchar(15) | → Product | |
| Description | varchar(64) | Omschrijving | |
| Orderdate | char(8) | Orderdatum YYYYMMDD | |
| OrderOrigin | varchar(4) | Herkomst | `06` (11.166.832), `12` (468.095), `11` (341.602) |
| ThisIinvoice | int | Aantal op deze factuur | |
| SalesPrice | decimal(18,2) | Verkoopprijs per stuk | |
| Discount1 / Discount2 | decimal(18,1) | Korting % | |
| InvoiceAmountFC | decimal(18,2) | Factuurbedrag vreemde valuta | |
| InvoiceAmountHC | decimal(18,2) | Factuurbedrag thuisvaluta (EUR) | |
| CostAmountBookedHC | decimal(18,2) | Geboekte kostprijs EUR | |
| VATCode | varchar(2) | BTW-code | `H` hoog (7.016.626), `0` geen (5.591.392) |
| Warehouse | varchar(2) | Warehouse | `CM` (12.607.997) |
| ExceptionCode | varchar(1) | Uitzonderingscode | ` ` (10.270.265), `E` (2.027.468), `Y` (121.471), `M` (99.318) |
| Unit | varchar(5) | Eenheid | `stk` (6.870.258), `pce` (3.840.725), `pcs` (1.861.769) |
| PurchasedAt | int | Ingekocht bij leverancier → CustSupplier | |

---

### 3.4 Inkoop

#### PurchaseOrder — Inkooporders (85.867 rijen)

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| Ordernr | int | **PK** | |
| Supplier | int | → CustSupplier.CustSuppnr (type 2) | |
| Date_ | char(8) | Orderdatum YYYYMMDD | |
| DeliveryDate | char(8) | Leverdatum YYYYMMDD | |
| Currency | varchar(3) | Valuta | ` ` (73.544), `EUR` (9.713), `GBP` (1.426) |
| Warehouse | varchar(2) | Warehouse | `CM` (85.862) |
| Completed | bit | Afgerond? | False (85.867) = alle open |

#### PurchaseOrderLines — Inkooporderregels (60.829 rijen)

| Kolom | Type | Beschrijving |
|-------|------|-------------|
| Ordernr | int | **PK** → PurchaseOrder |
| Orderline | int | **PK** |
| Product | varchar(15) | → Product |
| Price | decimal(18,2) | Inkoopprijs |
| Discount | decimal(18,1) | Korting % |
| ExpectedDelivery | char(8) | Verwachte levering YYYYMMDD |
| ConfirmPrice | varchar(1) | Prijsbevestiging | `1` bevestigd (60.025), `3` aangepast (728), `2` pending (76) |

#### PurchaseOrderInvoice — Leveranciersfacturen (71.764 rijen)

| Kolom | Type | Beschrijving |
|-------|------|-------------|
| Invoice | int | **PK** |
| Supplier | int | → CustSupplier |
| Date_ | char(8) | Factuurdatum |
| Currency | varchar(3) | Valuta |
| GoodsAmount | decimal(18,2) | Goederenbedrag |
| VATAmount | decimal(18,2) | BTW bedrag |

---

### 3.5 Warehouse & Voorraad

#### Warehouse — Warehousedefinities (12 rijen)

| Code | Omschrijving | Gebruik |
|------|-------------|---------|
| CM | Central warehouse | Hoofdmagazijn — 12M+ stock transacties |
| DS | Direct sales WH | Dropship warehouse — 4.8M transacties |
| IN | Inbound | Ontvangst/inbound — 1M transacties |
| OS | Overstock | Overstock opslag |
| SA | Samples | Monsters |
| DM | Damaged | Beschadigd |
| SC | Scrap | Afval/uitval |
| PR | Pricing | Prijsbepaling |
| ML | Manual Library | Handleidingen bibliotheek |
| TP | Transpose | Verplaatsing |
| PS | Parts Supply | Onderdelenvoorziening |
| MB | MB GW | MB Goodwill |

#### StockTransactions — Voorraadmutaties (17.982.513 rijen)

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| LogID | int | **PK** | |
| ProductID | varchar(15) | → Product | |
| Warehouse | varchar(2) | → Warehouse | `CM` (11.936.627), `DS` (4.854.746), `IN` (1.092.048) |
| Location | varchar(16) | Locatiecode in warehouse | |
| Date_ | char(8) | Datum YYYYMMDD | |
| Time_ | int | Tijd in seconden | |
| Employee | varchar(4) | → Employees | |
| TransactionType_ | varchar(1) | Type mutatie | `2` verkoop (10.063.006), `1` ontvangst (5.203.310), `4` verplaatsing (2.428.006), `5` (219.460), `8` (47.825), `6` (20.905) |
| StockFactor | int | Richting | +1 = toevoegen, -1 = afboeken |
| NumberOfItems | int | Aantal stuks | |
| CostPrice | decimal(16,4) | Kostprijs per stuk | |
| Booked | bit | Geboekt? | True (10.100.933), False (7.881.580) |
| FinPeriod | int | Periode YYYYMM | |

---

### 3.6 Betalingen

#### PaymentTrxLog — Betalingstransacties (1.462.625 rijen)

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| ID | int | **PK** | |
| Date_ | char(8) | Datum YYYYMMDD | |
| Time_ | int | Tijd in seconden | |
| Invoice | int | → Salesinvoice.Invoicenr | |
| Customer | int | → CustSupplier.CustSuppnr | |
| Amount | decimal(18,2) | Bedrag | |
| Status | int | Status | 5=succes (1.074.626), 9 (191.861), 81 (119.833), 2 (69.531), 0 (6.693) |
| PaymentProvider | tinyint | Provider | 0=legacy (1.004.617), 2=Adyen (384.909), 1=overig (73.104) |
| AdyenPaymentMethod | smallint | → AdyenPaymentMethods | |
| Currency | varchar(3) | Valuta | `EUR` (1.462.600) |

#### AdyenPaymentMethods — Betaalmethodes (23 rijen)

| PaymentMethod | Description | Naam |
|---------------|-------------|------|
| 1 | ideal | iDeal |
| 2 | paypal | PayPal |
| 3 | visa | Visa |
| 5 | mc | Mastercard |
| 6 | diners | Diners |
| 7 | discover | Discover |
| 8 | jcb | JCB |
| 9 | maestro | Maestro |
| 11 | amex | American Express |
| 13 | bcmc | Bancontact card |
| 14 | bcmc_mobile | Bancontact mobile |
| 15 | cartebancaire | Cartes Bancaires |
| 16 | cup | China UnionPay |

---

### 3.7 Medewerkers

#### Employees — Medewerkers (403 rijen)

| Kolom | Type | Beschrijving | Waarden |
|-------|------|-------------|---------|
| Code | varchar(4) | **PK** — Initialen (bijv. 'DD', 'CA') | |
| Name | varchar(30) | Naam | |
| Active | bit | Actief? | True (80), False (323) |
| Department | varchar(4) | Afdeling | ` ` (330), `MAG` magazijn (66), `ADM` admin (7) |

#### EmployeeContracts — Contracten (554 rijen)

Werktijden per medewerker. Uren opgeslagen in seconden (28800 = 8 uur).

---

### 3.8 Bulk operaties

#### BulkbatchHeader (30.053 rijen) — Batchheaders voor magazijnverwerking
#### Bulkbatchlines (1.484.635 rijen) — Batchregels met producten
#### BulkbatchChecks (1.388.558 rijen) — Kwaliteitscontroles per batch-item
#### BulkbatchPerformance (3.566.291 rijen) — Medewerkersprestaties per batch

#### BulkReceipts (741 rijen) — Bulkontvangsten van leveranciers
#### BulkReceiptsDetail (1.310.351 rijen) — Detailregels per bulkontvangst

---

### 3.9 Verzending

#### Cartons — Verzenddozen (1.544.371 rijen)

| Kolom | Type | Beschrijving |
|-------|------|-------------|
| Invoice | int | **PK** → Salesinvoice.Invoicenr |
| Cartonnr | int | **PK** — Doosnummer |
| TrackingID | varchar(34) | Track & trace code |
| ShipmentMethod | varchar(4) | Carrier |
| Height / Length / Width | decimal(18,2) | Afmetingen |
| Weight | decimal(18,3) | Gewicht |

**Verzendmethodes:**

| Code | Carrier |
|------|---------|
| FEDE | FedEx Standard |
| FEDP | FedEx Priority |
| UPSS | UPS Standard |
| UPWS | UPS WorldShip |
| DHLP | DHL Parcel |
| DHFY | DHL Freight |
| DHPC | DHL Parcel Connect |
| MER | Messenger/lokaal |

---

### 3.10 Overige tabellen

| Tabel | Rijen | Beschrijving |
|-------|-------|-------------|
| Currencies | 16 | Valutadefinities met wisselkoersen |
| Receipts | 10 | Open ontvangsten (niet-bulk) |
| ReceiptsDetail | 6.996 | Detailregels ontvangsten |
| ReceiptCalculatedBatchPurchaseList | 16.665 | Berekende batchinkooplijsten |
| ReceiptOriginalBatchPurchaseList | 220.103 | Originele batchinkooplijsten |
| ProductKits | 3.002 | Kit/bundelsamenstelling |
| ProductSupplierCostCalculat | 12 | Kostprijscalculaties |
| SupplierUpdateLog | 401 | Wijzigingslog leveranciers |
| PickingList | 126 | Actieve picklijsten |
| PickingListLines | 782 | Picklijstregels |
| CartonsPerPicklist | 8.870 | Dozen per picklijst |

---

## 4. Valuta's

| Code | Naam | Gebruik |
|------|------|---------|
| EUR | Euro | Primaire valuta (99%+ van transacties) |
| USD | US Dollar | Amerikaanse leveranciers |
| GBP | Brits Pond | Britse leveranciers |
| JPY | Japanse Yen | Japanse leveranciers |
| DKK | Deense Kroon | |
| THB | Thaise Baht | |
| CHF | Zwitserse Frank | |
| AUD | Australische Dollar | |
| CAD | Canadese Dollar | |
| NOK | Noorse Kroon | |
| SEK | Zweedse Kroon | |
| PLN | Poolse Zloty | |
| NZD | Nieuw-Zeelandse Dollar | |
| BGN | Bulgaarse Leva | |
| HUF | Hongaarse Forint | |
| TWD | Taiwanese Dollar | |
