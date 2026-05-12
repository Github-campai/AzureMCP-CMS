# ADMCMS Query Cookbook — Kant-en-klare SQL Queries

**Database:** ADMCMS-PRODUCTION-REPL01
**Doel:** Direct bruikbare queries voor rapportage en analyse
**Let op:** Alle datums zijn char(8) in YYYYMMDD formaat

---

## Instructies voor gebruik

- Kopieer een query en pas de datumfilters aan naar de gewenste periode
- Vervang `'20260101'` / `'20261231'` door je gewenste bereik
- Alle queries zijn SELECT-only en veilig voor de readonly replica
- Bij grote tabellen altijd een datumfilter gebruiken voor performance

---

## A. OMZET & FINANCIEEL

### A1. Totale omzet per maand (huidig jaar)

```sql
SELECT 
    LEFT(s.InvoiceDate, 6) AS Periode,
    COUNT(*) AS AantalFacturen,
    SUM(CASE WHEN s.InvoiceType = '0' THEN s.LineTotal ELSE 0 END) AS OmzetNormaal,
    SUM(CASE WHEN s.InvoiceType = '1' THEN s.LineTotal ELSE 0 END) AS OmzetCredit,
    SUM(s.LineTotal) AS OmzetTotaal,
    SUM(s.VATTotal) AS BTWTotaal,
    SUM(s.ShipCost) AS VerzendkostenTotaal
FROM Salesinvoice s
WHERE s.InvoiceDate >= '20260101' AND s.InvoiceDate <= '20261231'
GROUP BY LEFT(s.InvoiceDate, 6)
ORDER BY Periode
```

### A2. Omzetvergelijking jaar-op-jaar

```sql
SELECT 
    SUBSTRING(s.InvoiceDate, 5, 2) AS Maand,
    SUM(CASE WHEN LEFT(s.InvoiceDate, 4) = '2025' THEN s.LineTotal ELSE 0 END) AS Omzet2025,
    SUM(CASE WHEN LEFT(s.InvoiceDate, 4) = '2026' THEN s.LineTotal ELSE 0 END) AS Omzet2026,
    SUM(CASE WHEN LEFT(s.InvoiceDate, 4) = '2026' THEN s.LineTotal ELSE 0 END) -
    SUM(CASE WHEN LEFT(s.InvoiceDate, 4) = '2025' THEN s.LineTotal ELSE 0 END) AS Verschil
FROM Salesinvoice s
WHERE s.InvoiceDate >= '20250101' AND s.InvoiceDate <= '20261231'
    AND s.InvoiceType = '0'
GROUP BY SUBSTRING(s.InvoiceDate, 5, 2)
ORDER BY Maand
```

### A3. Omzet per kwartaal

```sql
SELECT 
    LEFT(s.InvoiceDate, 4) AS Jaar,
    CASE 
        WHEN SUBSTRING(s.InvoiceDate, 5, 2) IN ('01','02','03') THEN 'Q1'
        WHEN SUBSTRING(s.InvoiceDate, 5, 2) IN ('04','05','06') THEN 'Q2'
        WHEN SUBSTRING(s.InvoiceDate, 5, 2) IN ('07','08','09') THEN 'Q3'
        ELSE 'Q4'
    END AS Kwartaal,
    COUNT(*) AS AantalFacturen,
    SUM(s.LineTotal) AS Omzet,
    SUM(s.VATTotal) AS BTW
FROM Salesinvoice s
WHERE s.InvoiceDate >= '20240101' AND s.InvoiceType = '0'
GROUP BY LEFT(s.InvoiceDate, 4),
    CASE 
        WHEN SUBSTRING(s.InvoiceDate, 5, 2) IN ('01','02','03') THEN 'Q1'
        WHEN SUBSTRING(s.InvoiceDate, 5, 2) IN ('04','05','06') THEN 'Q2'
        WHEN SUBSTRING(s.InvoiceDate, 5, 2) IN ('07','08','09') THEN 'Q3'
        ELSE 'Q4'
    END
ORDER BY Jaar, Kwartaal
```

### A4. Gemiddelde orderwaarde per betaalmethode

```sql
SELECT 
    s.PaymentCondition,
    COUNT(*) AS AantalFacturen,
    SUM(s.LineTotal) AS TotaleOmzet,
    AVG(s.LineTotal) AS GemiddeldeOrderwaarde,
    MIN(s.LineTotal) AS KleinsteOrder,
    MAX(s.LineTotal) AS GrootsteOrder
FROM Salesinvoice s
WHERE s.InvoiceDate >= '20250101' AND s.InvoiceType = '0'
GROUP BY s.PaymentCondition
ORDER BY TotaleOmzet DESC
```

### A5. Creditnota-analyse

```sql
SELECT 
    LEFT(s.InvoiceDate, 6) AS Periode,
    COUNT(CASE WHEN s.InvoiceType = '0' THEN 1 END) AS NormaleFacturen,
    COUNT(CASE WHEN s.InvoiceType = '1' THEN 1 END) AS Creditnotas,
    SUM(CASE WHEN s.InvoiceType = '1' THEN s.LineTotal ELSE 0 END) AS CreditBedrag,
    CAST(COUNT(CASE WHEN s.InvoiceType = '1' THEN 1 END) AS FLOAT) / 
        NULLIF(COUNT(*), 0) * 100 AS CreditPercentage
FROM Salesinvoice s
WHERE s.InvoiceDate >= '20250101'
GROUP BY LEFT(s.InvoiceDate, 6)
ORDER BY Periode
```

### A6. Openstaande (onbetaalde) facturen

```sql
SELECT 
    s.Invoicenr, s.InvoiceDate, s.Customer,
    a.FullName AS KlantNaam,
    s.LineTotal, s.VATTotal, s.AmountHC,
    s.PaymentCondition
FROM Salesinvoice s
JOIN CustSupplier cs ON s.Customer = cs.CustSuppnr AND cs.CustSuppType = '1'
JOIN Addresses a ON cs.InvoiceAddressID = a.AddressID
WHERE s.FullyPaid = 0
    AND s.InvoiceType = '0'
ORDER BY s.InvoiceDate ASC
```

---

## B. KLANTEN

### B1. Top 20 klanten op omzet

```sql
SELECT TOP 20
    s.Customer,
    a.FullName AS KlantNaam,
    a.CityPA AS Stad,
    a.Country AS Land,
    COUNT(*) AS AantalFacturen,
    SUM(s.LineTotal) AS TotaleOmzet,
    MIN(s.InvoiceDate) AS EersteFactuur,
    MAX(s.InvoiceDate) AS LaatsteFactuur
FROM Salesinvoice s
JOIN CustSupplier cs ON s.Customer = cs.CustSuppnr AND cs.CustSuppType = '1'
JOIN Addresses a ON cs.InvoiceAddressID = a.AddressID
WHERE s.InvoiceDate >= '20250101' AND s.InvoiceType = '0'
GROUP BY s.Customer, a.FullName, a.CityPA, a.Country
ORDER BY TotaleOmzet DESC
```

### B2. Actieve klanten per land

```sql
SELECT 
    a.Country,
    COUNT(DISTINCT s.Customer) AS AantalKlanten,
    SUM(s.LineTotal) AS TotaleOmzet,
    AVG(s.LineTotal) AS GemiddeldeOrderwaarde
FROM Salesinvoice s
JOIN CustSupplier cs ON s.Customer = cs.CustSuppnr AND cs.CustSuppType = '1'
JOIN Addresses a ON cs.InvoiceAddressID = a.AddressID
WHERE s.InvoiceDate >= '20250101' AND s.InvoiceType = '0'
GROUP BY a.Country
ORDER BY TotaleOmzet DESC
```

### B3. Klanten met betalingsproblemen

```sql
SELECT 
    cs.CustSuppnr,
    a.FullName AS KlantNaam,
    a.Country,
    cs.OpenAmount,
    cs.Creditlimit,
    cs.PaymentCondition,
    cs.OnHoldReason,
    cs.LastTransaction
FROM CustSupplier cs
JOIN Addresses a ON cs.InvoiceAddressID = a.AddressID
WHERE cs.CustSuppType = '1'
    AND (cs.PaymentProblem = 1 OR cs.RedListed = 1 OR cs.OnHoldReason > 0)
ORDER BY cs.OpenAmount DESC
```

### B4. Klanten die dit jaar nog niet besteld hebben (churn risico)

```sql
SELECT 
    cs.CustSuppnr,
    a.FullName AS KlantNaam,
    a.Country,
    cs.LastTransaction AS LaatsteTransactie,
    cs.CustSuppGroup
FROM CustSupplier cs
JOIN Addresses a ON cs.InvoiceAddressID = a.AddressID
WHERE cs.CustSuppType = '1'
    AND cs.pActive = 1
    AND cs.InUse = 1
    AND cs.LastTransaction < '20260101'
    AND cs.LastTransaction > '20240101'
ORDER BY cs.LastTransaction DESC
```

### B5. Nieuwe klanten per maand

```sql
SELECT 
    LEFT(cs.CreationDate, 6) AS Periode,
    COUNT(*) AS NieuweKlanten
FROM CustSupplier cs
WHERE cs.CustSuppType = '1'
    AND cs.CreationDate >= '20250101'
    AND cs.CreationDate < '99999999'
GROUP BY LEFT(cs.CreationDate, 6)
ORDER BY Periode
```

### B6. Klant-detail: complete orderhistorie

```sql
-- Vervang 12345 door het klantnummer
SELECT 
    s.Invoicenr, s.InvoiceDate, s.InvoiceType,
    s.LineTotal, s.VATTotal, s.ShipCost,
    s.PaymentCondition, s.ShipmentMethod,
    s.FullyPaid, s.PickedBy, s.PackedBy
FROM Salesinvoice s
WHERE s.Customer = 12345
ORDER BY s.InvoiceDate DESC
```

---

## C. PRODUCTEN

### C1. Top 20 best verkopende producten

```sql
SELECT TOP 20
    sl.Productcode,
    p.Description,
    p.Productgroup,
    SUM(sl.ThisIinvoice) AS AantalVerkocht,
    SUM(sl.InvoiceAmountHC) AS Omzet,
    SUM(sl.CostAmountBookedHC) AS Kosten,
    SUM(sl.InvoiceAmountHC) - SUM(sl.CostAmountBookedHC) AS Marge
FROM SalesinvoiceLines sl
JOIN Salesinvoice s ON sl.Invoicenr = s.Invoicenr
JOIN Product p ON sl.Productcode = p.Productcode
WHERE s.InvoiceDate >= '20250101' AND s.InvoiceType = '0'
GROUP BY sl.Productcode, p.Description, p.Productgroup
ORDER BY Omzet DESC
```

### C2. Producten met negatieve marge

```sql
SELECT TOP 50
    sl.Productcode,
    p.Description,
    SUM(sl.ThisIinvoice) AS AantalVerkocht,
    SUM(sl.InvoiceAmountHC) AS Omzet,
    SUM(sl.CostAmountBookedHC) AS Kosten,
    SUM(sl.InvoiceAmountHC) - SUM(sl.CostAmountBookedHC) AS Marge,
    CASE WHEN SUM(sl.InvoiceAmountHC) > 0 
        THEN (SUM(sl.InvoiceAmountHC) - SUM(sl.CostAmountBookedHC)) / SUM(sl.InvoiceAmountHC) * 100 
        ELSE 0 END AS MargePercentage
FROM SalesinvoiceLines sl
JOIN Salesinvoice s ON sl.Invoicenr = s.Invoicenr
JOIN Product p ON sl.Productcode = p.Productcode
WHERE s.InvoiceDate >= '20250101' AND s.InvoiceType = '0'
GROUP BY sl.Productcode, p.Description
HAVING SUM(sl.InvoiceAmountHC) - SUM(sl.CostAmountBookedHC) < 0
ORDER BY Marge ASC
```

### C3. Productgroep-analyse

```sql
SELECT 
    p.Productgroup,
    COUNT(DISTINCT sl.Productcode) AS AantalProducten,
    SUM(sl.ThisIinvoice) AS AantalVerkocht,
    SUM(sl.InvoiceAmountHC) AS Omzet,
    SUM(sl.CostAmountBookedHC) AS Kosten,
    SUM(sl.InvoiceAmountHC) - SUM(sl.CostAmountBookedHC) AS Marge
FROM SalesinvoiceLines sl
JOIN Salesinvoice s ON sl.Invoicenr = s.Invoicenr
JOIN Product p ON sl.Productcode = p.Productcode
WHERE s.InvoiceDate >= '20250101' AND s.InvoiceType = '0'
GROUP BY p.Productgroup
ORDER BY Omzet DESC
```

### C4. Hot items overzicht

```sql
SELECT 
    p.Productcode, p.Description, p.Productgroup,
    p.Salesprice, p.Cost,
    pw.ActualStock, pw.Warehouse
FROM Product p
LEFT JOIN ProductWarehouse pw ON p.Productcode = pw.Productcode AND pw.Warehouse = 'CM'
WHERE p.HotItem = 1 AND p.InActiveProgram = 1
ORDER BY pw.ActualStock DESC
```

### C5. KIT-producten met onderdelen

```sql
SELECT 
    pk.SalesProductID AS KitProduct,
    p1.Description AS KitOmschrijving,
    pk.PartProductID AS Onderdeel,
    p2.Description AS OnderdeelOmschrijving,
    pk.NumberOfItem AS Aantal
FROM ProductKits pk
JOIN Product p1 ON pk.SalesProductID = p1.Productcode
JOIN Product p2 ON pk.PartProductID = p2.Productcode
ORDER BY pk.SalesProductID, pk.PartProductID
```

### C6. Producten met asbestrisico

```sql
SELECT 
    p.Productcode, p.Description, p.Productgroup,
    p.MayContainAsbestos, p.NonAsbestos, p.ToBeCheckedForAsbestos,
    pw.ActualStock
FROM Product p
LEFT JOIN ProductWarehouse pw ON p.Productcode = pw.Productcode AND pw.Warehouse = 'CM'
WHERE p.MayContainAsbestos = 1 OR p.ToBeCheckedForAsbestos = 1
ORDER BY pw.ActualStock DESC
```

---

## D. VOORRAAD

### D1. Voorraadwaarde per warehouse

```sql
SELECT 
    pw.Warehouse,
    w.Description AS WarehouseNaam,
    COUNT(*) AS AantalProducten,
    SUM(pw.ActualStock) AS TotaalStuks,
    SUM(pw.ActualStockValue) AS TotaleWaarde,
    AVG(pw.CostPrice) AS GemiddeldeCostPrice
FROM ProductWarehouse pw
JOIN Warehouse w ON pw.Warehouse = w.Code
WHERE pw.ActualStock > 0
GROUP BY pw.Warehouse, w.Description
ORDER BY TotaleWaarde DESC
```

### D2. Producten onder minimumvoorraad

```sql
SELECT 
    pw.Productcode, p.Description,
    pw.Warehouse, pw.ActualStock, pw.MinimumStock, pw.MaximumStock,
    pw.MinimumStock - pw.ActualStock AS Tekort
FROM ProductWarehouse pw
JOIN Product p ON pw.Productcode = p.Productcode
WHERE pw.ActualStock < pw.MinimumStock
    AND pw.MinimumStock > 0
    AND pw.Warehouse = 'CM'
    AND p.InActiveProgram = 1
ORDER BY (pw.MinimumStock - pw.ActualStock) DESC
```

### D3. Voorraad zonder beweging (dead stock)

```sql
SELECT 
    pw.Productcode, p.Description,
    pw.ActualStock, pw.ActualStockValue,
    MAX(st.Date_) AS LaatsteBeweging
FROM ProductWarehouse pw
JOIN Product p ON pw.Productcode = p.Productcode
LEFT JOIN StockTransactions st ON pw.Productcode = st.ProductID AND st.Warehouse = 'CM'
WHERE pw.Warehouse = 'CM' AND pw.ActualStock > 0
GROUP BY pw.Productcode, p.Description, pw.ActualStock, pw.ActualStockValue
HAVING MAX(st.Date_) < '20250101' OR MAX(st.Date_) IS NULL
ORDER BY pw.ActualStockValue DESC
```

### D4. Voorraadmutaties vandaag

```sql
SELECT 
    st.ProductID, p.Description,
    st.Warehouse, st.Location,
    st.TransactionType_, st.StockFactor, st.NumberOfItems,
    st.Time_ / 3600 AS Uur,
    (st.Time_ % 3600) / 60 AS Minuut,
    st.Employee
FROM StockTransactions st
JOIN Product p ON st.ProductID = p.Productcode
WHERE st.Date_ = FORMAT(GETDATE(), 'yyyyMMdd')
ORDER BY st.Time_ DESC
```

### D5. Beschadigde items overzicht

```sql
SELECT 
    pw.Productcode, p.Description,
    pw.ActualStock AS VoorraadDamaged,
    pw.ActualStockValue AS WaardeDamaged
FROM ProductWarehouse pw
JOIN Product p ON pw.Productcode = p.Productcode
WHERE pw.Warehouse = 'DM' AND pw.ActualStock > 0
ORDER BY pw.ActualStockValue DESC
```

---

## E. INKOOP

### E1. Top 10 leveranciers op inkoopvolume

```sql
SELECT TOP 10
    poi.Supplier,
    a.FullName AS LeverancierNaam,
    poi.Currency,
    COUNT(*) AS AantalFacturen,
    SUM(poi.GoodsAmount) AS TotaalGoederenBedrag
FROM PurchaseOrderInvoice poi
JOIN CustSupplier cs ON poi.Supplier = cs.CustSuppnr AND cs.CustSuppType = '2'
JOIN Addresses a ON cs.InvoiceAddressID = a.AddressID
WHERE poi.Date_ >= '20250101'
GROUP BY poi.Supplier, a.FullName, poi.Currency
ORDER BY TotaalGoederenBedrag DESC
```

### E2. Openstaande inkooporders

```sql
SELECT 
    po.Ordernr, po.Date_ AS OrderDatum,
    a.FullName AS Leverancier,
    po.Currency, po.DeliveryDate,
    COUNT(pol.Orderline) AS AantalRegels
FROM PurchaseOrder po
JOIN CustSupplier cs ON po.Supplier = cs.CustSuppnr AND cs.CustSuppType = '2'
JOIN Addresses a ON cs.InvoiceAddressID = a.AddressID
LEFT JOIN PurchaseOrderLines pol ON po.Ordernr = pol.Ordernr
WHERE po.Completed = 0
GROUP BY po.Ordernr, po.Date_, a.FullName, po.Currency, po.DeliveryDate
ORDER BY po.Date_ DESC
```

### E3. Inkoopprijshistorie per product

```sql
-- Vervang 'PRODUCTCODE' door het productcode
SELECT 
    poil.ReceivedDate,
    poi.Supplier,
    a.FullName AS Leverancier,
    poi.Currency,
    poil.Purchaseprice,
    poil.Discount,
    poil.InvoicedItems
FROM PurchaseOrderInvoiceLines poil
JOIN PurchaseOrderInvoice poi ON poil.Invoice = poi.Invoice
JOIN CustSupplier cs ON poi.Supplier = cs.CustSuppnr AND cs.CustSuppType = '2'
JOIN Addresses a ON cs.InvoiceAddressID = a.AddressID
WHERE poil.Product = 'PRODUCTCODE'
ORDER BY poil.ReceivedDate DESC
```

---

## F. BETALINGEN

### F1. Betalingen per methode (Adyen)

```sql
SELECT 
    apm.Description_Pretty AS Betaalmethode,
    COUNT(*) AS AantalTransacties,
    SUM(ptl.Amount) AS TotaalBedrag,
    AVG(ptl.Amount) AS GemiddeldBedrag,
    SUM(CASE WHEN ptl.Status = 5 THEN 1 ELSE 0 END) AS Geslaagd,
    SUM(CASE WHEN ptl.Status <> 5 THEN 1 ELSE 0 END) AS Mislukt,
    CAST(SUM(CASE WHEN ptl.Status = 5 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) * 100 AS SuccesPercentage
FROM PaymentTrxLog ptl
JOIN AdyenPaymentMethods apm ON ptl.AdyenPaymentMethod = apm.PaymentMethod
WHERE ptl.Date_ >= '20250101'
    AND ptl.PaymentProvider = 2
    AND ptl.TestTrx = 0
GROUP BY apm.Description_Pretty
ORDER BY AantalTransacties DESC
```

### F2. Betalingstrend per maand

```sql
SELECT 
    LEFT(ptl.Date_, 6) AS Periode,
    COUNT(*) AS AantalTransacties,
    SUM(ptl.Amount) AS TotaalBedrag,
    SUM(CASE WHEN ptl.Status = 5 THEN 1 ELSE 0 END) AS Geslaagd,
    SUM(CASE WHEN ptl.Status <> 5 THEN 1 ELSE 0 END) AS Mislukt
FROM PaymentTrxLog ptl
WHERE ptl.Date_ >= '20250101' AND ptl.TestTrx = 0
GROUP BY LEFT(ptl.Date_, 6)
ORDER BY Periode
```

---

## G. WAREHOUSE OPERATIES

### G1. Medewerkerproductiviteit (picks per dag)

```sql
SELECT 
    s.PickedBy AS Medewerker,
    e.Name AS Naam,
    LEFT(s.Shipdate, 6) AS Periode,
    COUNT(*) AS AantalFacturen
FROM Salesinvoice s
JOIN Employees e ON s.PickedBy = e.Code
WHERE s.Shipdate >= '20260101'
    AND s.PickedBy <> ' '
GROUP BY s.PickedBy, e.Name, LEFT(s.Shipdate, 6)
ORDER BY Periode, AantalFacturen DESC
```

### G2. Bulkbatch prestaties per medewerker

```sql
SELECT 
    bp.ByEmployee,
    e.Name,
    COUNT(*) AS AantalActiviteiten,
    SUM(bp.EndTime - bp.StartTime) AS TotaalTijdSeconden,
    AVG(bp.EndTime - bp.StartTime) AS GemiddeldeTijdSeconden
FROM BulkbatchPerformance bp
JOIN Employees e ON bp.ByEmployee = e.Code
WHERE bp.Date_ >= '20260101'
GROUP BY bp.ByEmployee, e.Name
ORDER BY AantalActiviteiten DESC
```

### G3. Kwaliteitscontrole — afkeurpercentage

```sql
SELECT 
    LEFT(bc.ApproveDate, 6) AS Periode,
    COUNT(*) AS TotaalChecks,
    SUM(CASE WHEN bc.Approved = 1 THEN 1 ELSE 0 END) AS Goedgekeurd,
    SUM(CASE WHEN bc.Approved = 0 THEN 1 ELSE 0 END) AS Afgekeurd,
    SUM(CASE WHEN bc.Damaged = 1 THEN 1 ELSE 0 END) AS Beschadigd,
    CAST(SUM(CASE WHEN bc.Approved = 0 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) * 100 AS AfkeurPercentage
FROM BulkbatchChecks bc
WHERE bc.ApproveDate >= '20250101' AND bc.ApproveDate < '99999999'
GROUP BY LEFT(bc.ApproveDate, 6)
ORDER BY Periode
```

### G4. Verzendingen per carrier

```sql
SELECT 
    s.ShipmentMethod,
    COUNT(*) AS AantalFacturen,
    SUM(s.ShipCost) AS TotaleVerzendkosten,
    AVG(s.ShipCost) AS GemiddeldeVerzendkosten
FROM Salesinvoice s
WHERE s.InvoiceDate >= '20250101'
    AND s.ShipmentMethod <> ' '
    AND s.InvoiceType = '0'
GROUP BY s.ShipmentMethod
ORDER BY AantalFacturen DESC
```

---

## H. PRODUCT PERFORMANCE

### H1. Product turnover trending (laatste 12 maanden)

```sql
SELECT 
    pt.Product, p.Description,
    pt.FinPeriod,
    pt.NumberOfPieces, pt.Turnover, pt.Cost,
    pt.Turnover - pt.Cost AS Marge
FROM ProductTurnover pt
JOIN Product p ON pt.Product = p.Productcode
WHERE pt.FinPeriod >= 202504
ORDER BY pt.Product, pt.FinPeriod
```

### H2. Producten met dalende omzet (vergelijking twee periodes)

```sql
SELECT 
    t1.Product, p.Description,
    t1.Omzet AS OmzetVorig,
    t2.Omzet AS OmzetHuidig,
    t2.Omzet - t1.Omzet AS Verschil,
    CASE WHEN t1.Omzet > 0 
        THEN (t2.Omzet - t1.Omzet) / t1.Omzet * 100 
        ELSE 0 END AS VerschilPercentage
FROM (
    SELECT Product, SUM(Turnover) AS Omzet
    FROM ProductTurnover
    WHERE FinPeriod >= 202401 AND FinPeriod <= 202412
    GROUP BY Product
) t1
JOIN (
    SELECT Product, SUM(Turnover) AS Omzet
    FROM ProductTurnover
    WHERE FinPeriod >= 202501 AND FinPeriod <= 202512
    GROUP BY Product
) t2 ON t1.Product = t2.Product
JOIN Product p ON t1.Product = p.Productcode
WHERE t1.Omzet > 100
    AND t2.Omzet < t1.Omzet * 0.8
ORDER BY Verschil ASC
```

---

## I. MANAGEMENT DASHBOARD QUERIES

### I1. KPI overzicht (huidige maand vs vorige maand)

```sql
SELECT 
    'Huidige maand' AS Periode,
    COUNT(*) AS AantalFacturen,
    SUM(s.LineTotal) AS Omzet,
    COUNT(DISTINCT s.Customer) AS UniekeKlanten,
    AVG(s.LineTotal) AS GemiddeldeOrderwaarde
FROM Salesinvoice s
WHERE s.InvoiceDate >= FORMAT(DATEADD(MONTH, 0, DATEADD(DAY, 1-DAY(GETDATE()), GETDATE())), 'yyyyMMdd')
    AND s.InvoiceType = '0'

UNION ALL

SELECT 
    'Vorige maand',
    COUNT(*),
    SUM(s.LineTotal),
    COUNT(DISTINCT s.Customer),
    AVG(s.LineTotal)
FROM Salesinvoice s
WHERE s.InvoiceDate >= FORMAT(DATEADD(MONTH, -1, DATEADD(DAY, 1-DAY(GETDATE()), GETDATE())), 'yyyyMMdd')
    AND s.InvoiceDate < FORMAT(DATEADD(DAY, 1-DAY(GETDATE()), GETDATE()), 'yyyyMMdd')
    AND s.InvoiceType = '0'
```

### I2. Dropshipment analyse

```sql
SELECT 
    LEFT(s.InvoiceDate, 6) AS Periode,
    COUNT(CASE WHEN sl.PurchasedAt > 0 THEN 1 END) AS DropshipOrders,
    COUNT(CASE WHEN sl.PurchasedAt = 0 THEN 1 END) AS StockOrders,
    SUM(CASE WHEN sl.PurchasedAt > 0 THEN sl.InvoiceAmountHC ELSE 0 END) AS DropshipOmzet,
    SUM(CASE WHEN sl.PurchasedAt = 0 THEN sl.InvoiceAmountHC ELSE 0 END) AS StockOmzet
FROM SalesinvoiceLines sl
JOIN Salesinvoice s ON sl.Invoicenr = s.Invoicenr
WHERE s.InvoiceDate >= '20250101' AND s.InvoiceType = '0'
GROUP BY LEFT(s.InvoiceDate, 6)
ORDER BY Periode
```

### I3. Taal/regio analyse van klantbestand

```sql
SELECT 
    a.Language,
    a.Country,
    COUNT(DISTINCT cs.CustSuppnr) AS AantalKlanten,
    SUM(cs.OpenAmount) AS TotaalOpenstaand
FROM CustSupplier cs
JOIN Addresses a ON cs.InvoiceAddressID = a.AddressID
WHERE cs.CustSuppType = '1' AND cs.pActive = 1 AND cs.InUse = 1
GROUP BY a.Language, a.Country
ORDER BY AantalKlanten DESC
```

---

## Handige vragen om aan Claude te stellen

Hieronder een lijst met vragen die je in natuurlijke taal aan Claude kunt stellen via de MCP server:

### Dagelijks
- Hoeveel orders zijn er vandaag binnengekomen?
- Wat is de omzet van vandaag?
- Zijn er facturen die niet volledig betaald zijn?
- Hoeveel picks zijn er vandaag gedaan en door wie?

### Wekelijks
- Vergelijk de omzet van deze week met vorige week
- Welke producten zijn onder minimumvoorraad gezakt?
- Hoeveel nieuwe klanten zijn er deze week bijgekomen?
- Wat is het afkeurpercentage in de bulkbatch checks deze week?

### Maandelijks
- Geef een omzetoverzicht per land voor afgelopen maand
- Wat zijn de top 20 producten op marge deze maand?
- Welke klanten hebben betalingsproblemen?
- Hoe presteert iDeal vs PayPal vs creditcard qua volume?
- Wat is de voorraadwaarde per warehouse?

### Kwartaal/Jaar
- Vergelijk Q1 dit jaar met Q1 vorig jaar
- Welke productgroepen groeien en welke krimpen?
- Wat is het klantverloop (churn)?
- Wat is de totale voorraadwaarde en hoe verhoudt die zich tot de omzet?
- Welke leveranciers leveren de meeste producten?

### Ad-hoc analyse
- Zoek alle orders van klant [naam/nummer]
- Wat is de inkoopprijshistorie van product [code]?
- Hoeveel dead stock hebben we (geen beweging > 12 maanden)?
- Welke producten hebben een negatieve marge?
- Hoeveel producten bevatten mogelijk asbest?
