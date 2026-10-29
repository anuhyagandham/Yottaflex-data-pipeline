# Pipeline & Database Reconciliation Audit Report

## 1. Executive Summary
- **Overall Audit Status**: **PASSED**
- **Total Customers Ingested**: `45`
- **Total Orders Ingested**: `94`
- **Total Records Quarantined**: `36`
- **Pipeline Runs Logged**: `8`

## 2. Per-Run Conservation Law Invariants
## 3. Database State & Referential Integrity Invariants
| Check | Expected | Actual | Status | Description |
|:---|:---:|:---:|:---:|:---|
| `CUSTOMERS_PK_UNIQUENESS` | 45 | 45 | PASS | Every customer record must have a unique customer_id |
| `ORDERS_PK_UNIQUENESS` | 94 | 94 | PASS | Every order record must have a unique order_id |
| `ORDERS_FK_REFERENTIAL_INTEGRITY` | 0 | 0 | PASS | Zero orders may exist referencing non-existent customer_ids |
| `ORDERS_AMOUNT_NON_NEGATIVE` | 0 | 0 | PASS | Zero orders may have negative amount |
| `ORDERS_STATUS_DOMAIN_INTEGRITY` | 0 | 0 | PASS | All order statuses must belong to the documented allowed set |
| `ORDERS_AMOUNT_CENTS_DERIVATION` | 0 | 0 | PASS | Derived amount_cents must exactly match integer conversion of amount * 100 |
