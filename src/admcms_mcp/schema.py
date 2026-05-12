"""Static knowledge about the ADMCMS database schema.

This string is embedded into MCP resources so Claude can reason about
tables and query patterns without hitting the DB repeatedly.
"""

SCHEMA_KNOWLEDGE = """
# ADMCMS Database — Complete Schema Reference

## CRITICAL: Date & Time Handling
- ALL dates are stored as char(8) in YYYYMMDD format (e.g. '20260406')
- Empty/unknown dates = '00000000'
- To filter by date range: WHERE InvoiceDate >= '20250101' AND InvoiceDate <= '20251231'
- To get current year: WHERE InvoiceDate >= '20260101'
- To convert for display: use CONVERT or LEFT/SUBSTRING to extract parts
- TIME values are stored as int = seconds since midnight (e.g. 37029 = 10:17:09)
- To convert time: Time_ / 3600 = hours, (Time_ % 3600) / 60 = minutes
- FinPeriod is stored as int in YYYYMM format (e.g. 202604)

## CRITICAL: No Foreign Keys Defined
All relationships are IMPLICIT via naming conventions:
- CustSupplier.CustSuppnr → Salesinvoice.Customer, SalesOrderHeader.Customer, etc.
- Product.Productcode → SalesinvoiceLines.Productcode, ProductWarehouse.Productcode, etc.
- Warehouse.Code → ProductWarehouse.Warehouse, StockTransactions.Warehouse, etc.
- Salesinvoice.Invoicenr → SalesinvoiceLines.Invoicenr, Cartons.Invoice, etc.
- Addresses.AddressID → CustSupplier.InvoiceAddressID, .DefdeliveryAddressID, etc.
- Employees.Code → various ByEmployee, PickedBy, PackedBy, etc. fields

## COMMON COLUMN-NAME MISTAKES (DO NOT MAKE THESE)
These columns DO NOT EXIST — using them will fail with "Invalid column name":

Salesinvoice:
- CustSuppnr → use `Customer` (FK to CustSupplier.CustSuppnr)
- InvoiceTotal → use `LineTotal` (ex-VAT) or `AmountHC` (incl, home currency)

SalesinvoiceLines:
- Linenr → column is `Orderline`
- Quantity → column is `ThisIinvoice` (qty invoiced on this line)
- UnitPrice → column is `SalesPrice`
- DiscountPercent → there is no percent column; use `Discount1`, `Discount2`, or `DiscountAmount`
- Costprice / CostPrice → column is `CostAmountBookedHC`
- VAT → no amount column; use `VATCode` (H / 0 / etc.) or join Salesinvoice.VATTotal
- LineTotal → NOT on lines; on Salesinvoice header. On lines use `InvoiceAmountHC`.

SalesOrderHeader:
- Invoicenr → not on the header; use `SalesOrdersystemStatus` for open/closed filtering
- TotalAmount → column is `OrderAmount`
- ShippingDate → column is `DeliveryDate`

Any transaction table:
- Company / Mandant → ADMCMS has NO company dim on transaction tables

Rule of thumb: before writing raw SQL with query_database, ALWAYS call get_schema on
the table(s) first. Guessing column names based on SQL conventions from other systems
fails because ADMCMS uses its own naming (e.g., `Customer` not `CustSuppnr`,
`SalesPrice` not `UnitPrice`, `ThisIinvoice` not `Quantity`).

## Tables Overview (45 tables, 82M+ rows)

### CUSTOMERS & ADDRESSES

**CustSupplier** (478K rows) — Master customer/supplier table
- PK: CustSuppnr + CustSuppType
- CustSuppType: '1' = customer (477K), '2' = supplier (1.5K)
- CustSuppGroup: 'ND' (standard, 472K), 'G2' (4.5K), 'JBL', 'POA', etc. (24 groups)
- PaymentCondition: 'PREP' (prepaid, 421K), 'CC' (credit card, 44K), 'AC' (account, 8.6K), 'LINK', 'ACC', etc.
- DeliveryCondition: '' (478K), 'EXW', 'SD', 'FRA', 'FOB', 'CIF'
- Currency: 'EUR' (478K dominant), 'USD', 'GBP', 'JPY', etc.
- Links to Addresses via InvoiceAddressID, DefdeliveryAddressID
- InvoicingOrderingType: '2' (475K), '1' (3.5K)
- OnHoldReason: 0=none, 1-8 various holds
- WebAccountType: 1 (467K), 0 (10.7K), 5 (498)

**Addresses** (574K rows) — Address records
- PK: AddressID
- Type: '1' = main/invoice (12K), '2' = delivery (562K)
- Country: 152 countries (2-letter ISO codes)
- Language: 'E' (English, 334K), 'NL' (78K), 'D' (67K), 'F' (66K), 'IT' (23K), 'JP' (4K)
- AR = Accounts Receivable flag, AP = Accounts Payable flag

**AddressContacts** (565K rows) — Contact persons per address
- PK: AddressID + ContactID
- Gender: '1' = male (424K), '2' = female (13K), '3' = unknown (128K)

**AddressRemarks** (106K rows) — Notes/remarks per address
- PK: AddressID + Line
- DateEntered/DateChanged: char(8) YYYYMMDD, range 2004-2026

### PRODUCTS & PRICING

**Product** (5.2M rows) — Master product table (automotive aftermarket parts)
- PK: Productcode (varchar 15, e.g. '37250MBZD01')
- Type: product type code (varchar 7)
- Code: product code (varchar 7)
- Model: model reference (varchar 15)
- Make: manufacturer code (varchar 2)
- Productgroup: 3-char group code
- Unit: 'pcs' (2.7M), 'pce' (1.6M), 'stk' (857K)
- Availability: 'D' = available (3.2M), 'G' (1.97M), '' (12K)
- Region: '0' (3.45M), '1' (1.53M), '2' (214K)
- Salesproductcode: 'M' (2.2M), '' (1.3M), 'A' (1.06M), 'O', 'P', 'D', 'J', 'S', 'B', 'G', 'C', 'H'
- Salesprice, Cost, MaxDiscount, SpecialPrice: pricing fields (decimal)
- InActiveProgram: True = active (5.18M), False = inactive (19K)
- HotItem, KIT, FreezeProduct: boolean flags
- Discountgroup: '' (5.1M), 'CMS01'-'CMS55' (various discount tiers)
- Asbestos flags: MayContainAsbestos, NonAsbestos, ToBeCheckedForAsbestos
- CreationDate, LastModificationDate: char(8) YYYYMMDD

**ProductSupplier** (16.6M rows) — Product-supplier links
- PK: Product + Supplier
- Active: True (16.6M), False (64K)
- Currency: 'EUR' (13.9M), 'USD' (1.45M), 'GBP' (661K), 'JPY' (405K), 'DKK', 'THB', etc.
- Purchaseprice, PurchaseDiscount, LastKnownStock, MinimumOrderQty

**ProductWarehouse** (1M rows) — Stock levels per warehouse
- PK: Productcode + Warehouse
- Warehouse: 'CM' (525K), 'DS' (311K), 'IN' (133K), 'OS' (27K), 'SA', 'DM', 'SC', 'ML', 'PR', 'TP'
- ActualStock, ActualStockValue, CostPrice, MinimumStock, MaximumStock

**ProductTurnover** (6M rows) — Turnover per product per financial period
- PK: Product + FinPeriod
- FinPeriod: int YYYYMM format (e.g. 202604)
- NumberOfPieces, Turnover, Cost, NoSales, QueryHits

**ProductKits** (3K rows) — Kit/bundle definitions
- PK: SalesProductID + PartProductID

**PriceProdCustgrp** (9K rows) — Product-specific prices per customer group
- PK: Product + Customergroup
- 24 customer groups: G0, G1, G2, G3, G4, G5, G6, G10, G15, G20, G25, G30, G32, G32A, G35, G42, G42A, G42B, G48, G50

**PriceProdgrpCustgrp** (3.5K rows) — Product GROUP prices per customer group
**ProdgrCustgrpSuppliers** (5K rows) — Supplier assignments per product group/customer group

### SALES

**SalesOrderHeader** (6.9K rows) — Open/recent sales orders
- PK: Ordernr
- Customer → CustSupplier.CustSuppnr
- OrderOrigin: '12' (3.6K), '06' (1.9K), '11' (1.3K)
- PaymentCondition: 'PREP' (3.7K), 'AC' (2.2K), 'LINK' (855), 'ACC' (212)
- PreferredShipment: '' (4.7K), 'UPSS', 'FEDE', 'DHFY', 'DHPC', 'FEDP', 'UPWS', 'DHLP'
- DropShipment: False (5.6K), True (1.3K)
- Currency: 'EUR' (all), Warehouse: 'CM' (all)

**SalesOrderLines** (28K rows) — Order lines
- PK: Ordernr + Linenr
- Productcode, Description, Warehouse
- SalesPrice, Discount1, Discount2, DiscountAmount
- OpenOrder, Invoiced, ToDeliverFromStock, ToPurchase, Purchased
- VATCode: 'H' = high (28K)
- CancelReason: '' (27.7K), '1' (163), '3' (11), etc.

**Salesinvoice** (1.6M rows) — Sales invoices (the big one!)
- PK: Invoicenr
- InvoiceType: '0' = normal (1.58M), '1' = credit note (26K)
- Customer → CustSupplier.CustSuppnr
- InvoiceDate: char(8) YYYYMMDD
- FinPeriod: int YYYYMM
- Currency: 'EUR' (1.6M)
- PaymentCondition: 'PREP' (1.04M), 'AC' (267K), 'CC' (239K), 'ACD' (28K), 'ACC' (16K), 'RFI' (15K), 'LINK' (7.5K)
- ShipmentMethod: 'FEDE', 'UPSS', 'DHLP', etc.
- LineTotal, VATTotal, ShipCost, AmountHC
- FullyPaid: True (1.61M), False (741)
- Processed: True (1.61M), False (112)
- PickedBy, PackedBy, PreppedBy, CheckedBy → Employees.Code

**SalesinvoiceLines** (12.6M rows) — Invoice detail lines
- PK: Invoicenr + Ordernumber + Orderline + MasterOrderline
- Productcode, Description, Unit, Warehouse
- SalesPrice, Discount1, Discount2, DiscountAmount
- InvoiceAmountFC, InvoiceAmountHC, CostAmountBookedHC
- OrderOrigin: '06' (11.2M dominant), '12' (468K), '11' (342K), '' (260K), '01' (240K)
- VATCode: 'H' (7M), '0' (5.6M)
- ExceptionCode: '' (10.3M), 'E' (2M), 'Y' (121K), 'M' (99K), 'X' (89K)
- Warehouse: 'CM' (12.6M)
- ThisIinvoice = quantity on this invoice
- BackOrder, NonDelivered, ItemsCancelled

### PURCHASING

**PurchaseOrder** (86K rows) — Purchase orders to suppliers
- PK: Ordernr
- Supplier → CustSupplier.CustSuppnr (where CustSuppType='2')
- Currency: '' (73K), 'EUR' (10K), 'GBP' (1.4K), 'USD', 'JPY'
- Warehouse: 'CM' (86K)
- Date_, DeliveryDate: char(8) YYYYMMDD

**PurchaseOrderLines** (61K rows) — PO detail lines
- PK: Ordernr + Orderline
- Product, Description, SupplierProductcode
- Price, Discount, ExpectedDelivery: char(8) YYYYMMDD
- ConfirmPrice: '1' (60K confirmed), '3' (728 adjusted), '2' (76 pending)

**PurchaseOrderInvoice** (72K rows) — Supplier invoices
- PK: Invoice
- Currency: 'EUR' (62K), 'GBP' (6.4K), 'USD' (1.7K), 'JPY' (1.7K)
- GoodsAmount, VATAmount, OtherCostTotal

**PurchaseOrderInvoiceLines** (3.8M rows) — Supplier invoice details
**PurchaseOrderInvoices** (4.2M rows) — PO-Invoice linkage
**PurchaseOrderlineDetail** (163K rows) — Sales order to PO linkage

### WAREHOUSE & STOCK

**Warehouse** (12 rows) — Warehouse definitions
- PK: Code
- CM = Central warehouse, DS = Direct sales, IN = Inbound
- OS = Overstock, SA = Samples, DM = Damaged
- SC = Scrap, PR = Pricing, ML = Manual Library
- TP = Transpose, PS = Parts Supply, MB = MB GW

**StockTransactions** (18M rows) — Stock movement log
- PK: LogID
- ProductID, Warehouse, Location
- Date_: char(8) YYYYMMDD, Time_: int seconds since midnight
- TransactionType_: '2' (10M sell), '1' (5.2M receive), '4' (2.4M transfer), '5' (219K), '8' (48K), '6' (21K)
- StockFactor: +1 or -1 (direction)
- NumberOfItems, CostPrice
- Booked: True (10.1M), False (7.9M)
- FinPeriod: int YYYYMM

**Cartons** (1.5M rows) — Shipment cartons
- PK: Cartonnr + Invoice
- TrackingID, ShipmentMethod, Height, Length, Width, Weight

**CartonsPerPicklist** (9K rows) — Cartons linked to picklists

### BULK OPERATIONS (warehouse batch processing)

**BulkbatchHeader** (30K rows) — Batch headers
- Type: 0 (30K normal), 1 (29 special)

**Bulkbatchlines** (1.5M rows) — Batch line items
**BulkbatchChecks** (1.4M rows) — Quality checks per batch item
**BulkbatchPerformance** (3.6M rows) — Employee performance tracking per batch

**BulkReceipts** (741 rows) — Bulk receipt headers
**BulkReceiptsDetail** (1.3M rows) — Bulk receipt detail lines

### PAYMENTS

**PaymentTrxLog** (1.5M rows) — Payment transaction log
- PK: ID
- Date_: char(8), Time_: int seconds
- Invoice → Salesinvoice.Invoicenr, Customer → CustSupplier.CustSuppnr
- Status: 5 (1.07M success), 9 (192K), 81 (120K), 2 (70K), 0 (7K)
- PaymentProvider: 0 (1M legacy), 2 (385K Adyen), 1 (73K other)
- AdyenPaymentMethod → AdyenPaymentMethods.PaymentMethod
- Amount, Currency: 'EUR'

**AdyenPaymentMethods** (23 rows) — Payment method lookup
- 1=iDeal, 2=PayPal, 3=Visa, 5=Mastercard, 6=Diners, etc.

### EMPLOYEES

**Employees** (403 rows) — Employee master
- PK: Code (varchar 4, e.g. 'DD', 'CA', 'GP')
- Active: True (80), False (323)
- Department: '' (330), 'MAG' (66 warehouse), 'ADM' (7 admin)

**EmployeeContracts** (554 rows) — Working hours contracts
- Hours stored in seconds (28800 = 8 hours)

### RECEIPTS (regular, non-bulk)

**Receipts** (10 rows) — Current open receipts
**ReceiptsDetail** (7K rows) — Receipt detail lines
**ReceiptCalculatedBatchPurchaseList** (17K rows)
**ReceiptOriginalBatchPurchaseList** (220K rows)

### OTHER

**Currencies** (16 rows) — Currency definitions with exchange rates
- EUR, USD, GBP, JPY, DKK, THB, CHF, AUD, CAD, NOK, SEK, PLN, NZD, BGN, HUF, TWD

**SupplierUpdateLog** (401 rows) — Supplier data change log

## Shipping Methods
- FEDE = FedEx, FEDP = FedEx Priority
- UPSS = UPS Standard, UPWS = UPS WorldShip
- DHLP = DHL Parcel, DHFY = DHL Freight, DHPC = DHL Parcel Connect
- MER = Messenger/local delivery

## Common Query Patterns

### Sales turnover for a period:
SELECT SUM(InvoiceAmountHC) as TotalTurnover, COUNT(*) as InvoiceCount
FROM Salesinvoice
WHERE InvoiceDate >= '20250101' AND InvoiceDate <= '20251231'
AND InvoiceType = '0'  -- exclude credit notes

### Top products by revenue:
SELECT TOP 20 sl.Productcode, p.Description, SUM(sl.InvoiceAmountHC) as Revenue, SUM(sl.ThisIinvoice) as Qty
FROM SalesinvoiceLines sl
JOIN Salesinvoice s ON sl.Invoicenr = s.Invoicenr
JOIN Product p ON sl.Productcode = p.Productcode
WHERE s.InvoiceDate >= '20250101' AND s.InvoiceDate <= '20251231'
GROUP BY sl.Productcode, p.Description
ORDER BY Revenue DESC

### Current stock levels:
SELECT pw.Productcode, p.Description, pw.Warehouse, pw.ActualStock, pw.CostPrice
FROM ProductWarehouse pw
JOIN Product p ON pw.Productcode = p.Productcode
WHERE pw.Warehouse = 'CM' AND pw.ActualStock > 0
ORDER BY pw.ActualStock DESC

### Customer order history:
SELECT s.Invoicenr, s.InvoiceDate, s.LineTotal, s.VATTotal, s.ShipCost,
       s.PaymentCondition, s.FullyPaid
FROM Salesinvoice s
WHERE s.Customer = @CustomerNr
ORDER BY s.InvoiceDate DESC

### Stock movements for a product:
SELECT st.Date_, st.TransactionType_, st.NumberOfItems, st.StockFactor,
       st.Warehouse, st.Location, st.CostPrice
FROM StockTransactions st
WHERE st.ProductID = @ProductCode
ORDER BY st.Date_ DESC, st.Time_ DESC
"""
