# YottaFlex Reusable Data Engineering Pipeline Framework

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/pytest-31%20passed%20(100%25)-brightgreen.svg)]()
[![Throughput](https://img.shields.io/badge/throughput-16%2C730%20rec%2Fsec-success.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A production-grade, highly resilient, and idempotent ETL framework built in Python for the **YottaFlex.ai Associate AI Forward Deployed Engineer** role submission.

The framework provides parameterized extraction, two-tier validation (structural schema + domain business rules), row-level quarantine traceability, deterministic in-batch deduplication, atomic SQLite persistence via a staging-merge pattern, automated mathematical reconciliation, and sub-millisecond per-batch throughput.

---

## Architecture Overview

```mermaid
flowchart TD
    subgraph Ingestion ["1. Extraction & Drift Detection"]
        CSV[Source CSV File] --> Extractor[CSVExtractor]
        Extractor -->|Malformed Rows| Q1[Quarantine Records]
        Extractor -->|Schema Drift Warning| DriftLog[Drift Log]
        Extractor -->|Parsed Records| SVal[SchemaValidator]
    end

    subgraph Validation ["2. Two-Tier Validation"]
        SVal -->|Missing / Null Fields| Q2[Quarantine Records]
        SVal -->|Valid Schema| BVal[BusinessValidator]
        BVal -->|Bad Email / Amount / Status| Q3[Quarantine Records]
        BVal -->|Orphan Customer FK| Q3
        BVal -->|Valid Records| Trans[DataTransformer]
    end

    subgraph Transformation ["3. Transformation & In-Batch Dedup"]
        Trans -->|Deterministic Dedup| Dedup[Version Conflict Resolver]
        Trans -->|Enrichment| Enrich[Derive amount_cents & Audit Tags]
        Dedup --> TransRecords[Transformed Batch]
        Enrich --> TransRecords
    end

    subgraph Loading ["4. Atomic Staging Upsert"]
        TransRecords --> Staging[Temp Staging Table]
        Staging --> Disjoint[Partition: Insert / Update / Skip]
        Disjoint --> LoadTarget[(Target SQLite DB)]
    end

    subgraph Audit ["5. Reconciliation & Observability"]
        LoadTarget --> Reconcile[ReconciliationEngine]
        Q1 & Q2 & Q3 --> QuarantineDB[(quarantine_records)]
        Reconcile --> AuditLog[Conservation Laws & DB Invariants]
        AuditLog --> RunsDB[(pipeline_runs)]
        RunsDB --> Report[Markdown / CLI Audit Report]
    end
```

---

## Key Features & Engineering Judgment

1. **Reusable ETL Framework**:
   All dataset rules (field types, nullability, regex patterns, enum bounds, foreign keys, and DDLs) reside in declarative `DatasetConfig` objects in [`src/config.py`](file:///c:/Users/Gandham%20Anuhya/OneDrive/Desktop/YottaFlex-Data-Pipeline/src/config.py), eliminating duplicated pipeline code.
2. **Zero Silent Drops & Row-Level Quarantine**:
   Invalid rows (malformed CSV rows, missing non-nullable attributes, invalid emails, negative amounts, or orphan foreign keys) are never silently discarded. Every rejected record is captured with its `run_id`, source line number, raw payload JSON, rejection stage, error code, and timestamp into `quarantine_records`.
3. **True Idempotency & Version Conflict Resolution**:
   Uses an atomic **Staging Table Merge** (`load.py`). Re-running the exact same file 10 times results in `0 inserted`, `0 updated`, and `N skipped`, producing zero unintended duplicate rows. If an update arrives, newer timestamps overwrite older data and increment `_version`; older timestamps are skipped to prevent regression.
4. **Mathematical Conservation Laws**:
   Automated reconciliation verifies that:
   - $\text{Input Rows} = \text{Valid Rows} + \text{Rejected Rows}$
   - $\text{Valid Rows} = \text{Inserted Rows} + \text{Updated Rows} + \text{Skipped Rows} + \text{In-Batch Dups}$
   - Database foreign-key referential integrity is 100% sound (0 orphan orders).
5. **High-Performance Architecture**:
   Employs SQLite Write-Ahead Logging (`WAL`), `temp_store = MEMORY`, and parameterized `executemany` batching, delivering **16,730+ records/sec** overall throughput and a **189.3x speedup** over unbatched row-by-row commits.

---

## Repository Structure

```
YottaFlex-Data-Pipeline/
├── src/
│   ├── __init__.py           # Package version and export definitions
│   ├── config.py             # Declarative schema rules, DDLs, and dataset configs
│   ├── extract.py            # Reusable CSV extractor with malformed row & drift tolerance
│   ├── validate.py           # SchemaValidator and BusinessValidator engines
│   ├── transform.py          # Field normalization, derived attributes & in-batch deduplication
│   ├── load.py               # SQLiteLoader with staging table merge, retries & transactions
│   ├── quarantine.py         # QuarantineRecord dataclass and QuarantineManager
│   ├── logging_config.py     # Structured logging with run_id propagation
│   ├── metrics.py            # PipelineMetrics tracking elapsed time and throughput
│   ├── reconcile.py          # ReconciliationEngine checking conservation laws & DB invariants
│   ├── generator.py          # Seeded synthetic generator for sample and 100k+ benchmarks
│   ├── pipeline.py           # Pipeline orchestrator and dependency suite runner
│   └── cli.py                # Command-line interface with rich ASCII reports
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Shared fixtures: isolated temp databases and sample CSVs
│   ├── test_extract.py       # Extraction, BOM, malformed rows, and drift tests
│   ├── test_validate.py      # Schema and domain business validation tests
│   ├── test_transform.py     # Field derivations and in-batch deduplication tests
│   ├── test_load.py          # Staging merge upsert, rollback, and retry tests
│   ├── test_pipeline.py      # End-to-end single and multi-dataset pipeline tests
│   ├── test_idempotency.py   # Replay idempotency and incremental overlap tests
│   ├── test_reconcile.py     # Conservation law arithmetic and database constraint audits
│   └── test_generator.py     # Synthetic data generation and seed reproducibility tests
├── data/
│   └── sample/               # Sample CSVs with deliberate edge cases
├── output/                   # Target SQLite database (pipeline.db) and logs
├── reports/                  # Generated Markdown reconciliation audit reports
├── docs/                     # Additional architectural references
├── README.md                 # Project guide, quickstart, and design overview
├── DATA_DICTIONARY.md        # Comprehensive data dictionary of all fields & constraints
├── DESIGN_NOTES.md           # Engineering decisions, trade-offs, and optimization benchmarks
├── AI_PROMPT_HISTORY.md      # Transparent record of prompts used during pair-programming
├── requirements.txt          # Minimal dependencies (pytest)
└── .gitignore                # Git ignore hygiene
```

---

## Installation & Setup (Windows)

The project requires **Python 3.11+** (tested and verified on Python 3.13.14 on Windows 11).

### 1. Clone & Enter Project Directory
```powershell
cd "c:\Users\Gandham Anuhya\OneDrive\Desktop\YottaFlex-Data-Pipeline"
```

### 2. (Optional) Create & Activate a Virtual Environment
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install Dependencies
The core pipeline uses the Python standard library. Only `pytest` is required for testing:
```powershell
python -m pip install -r requirements.txt
```

---

## CLI Execution Guide

The framework includes a CLI via [`src/cli.py`](file:///c:/Users/Gandham%20Anuhya/OneDrive/Desktop/YottaFlex-Data-Pipeline/src/cli.py).

### 1. Generate Sample Datasets (with injected edge cases)
Generates `data/sample/customers_sample.csv` (50 rows) and `data/sample/orders_sample.csv` (100 rows) with deliberate edge cases (malformed lines, duplicate keys, invalid emails, negative amounts, orphan customer IDs, and schema drift):
```powershell
python -m src.cli generate-sample
```

### 2. Run Both Pipelines in Dependency Order
Executes `customers` first, then `orders`, enforcing referential integrity and saving to `output/pipeline.db`:
```powershell
python -m src.cli run --dataset all
```

### 3. Verify Idempotency (Re-running the exact same files)
Execute the exact same command again:
```powershell
python -m src.cli run --dataset all
```
*Notice that `DB Inserted` is `0`, `DB Updated` is `0`, and all rows are reported as `DB Skipped`! No duplicate records are created.*

### 4. Run Global Database Reconciliation Audit
Audits primary key uniqueness, foreign key integrity, non-negative amounts, and allowed statuses, generating a Markdown report in `reports/reconciliation_report.md`:
```powershell
python -m src.cli reconcile
```

### 5. Inspect Historical Runs & Quarantine Breakdown
Displays an ASCII summary of all pipeline executions and a breakdown of quarantined records grouped by stage and error code:
```powershell
python -m src.cli report
```

---

## Verified Execution Results

### 1. Automated Test Suite (pytest)
Executed across 31 comprehensive unit, integration, failure-recovery, and idempotency tests:
```powershell
python -m pytest -v
```
**Actual Result**:
```
======================== 31 passed, 1 warning in 2.26s ========================
```

### 2. Pipeline Execution on Sample Data
```
=================================================================
          PIPELINE SUITE EXECUTION COMPLETED
=================================================================
 Database: output\pipeline.db

============================================================
 PIPELINE RUN METRICS SUMMARY: run_customers_9b1774d7
============================================================
 Dataset       : customers
 Source File   : customers_sample.csv
 Status        : PARTIAL_SUCCESS
 Elapsed (sec) : 0.0550 s
 Throughput    : 909.09 records/sec
------------------------------------------------------------
 Input Rows    : 50
 Valid Rows    : 46
 Rejected Rows : 4 (1 malformed, 1 missing email, 1 bad email, 1 bad timestamp)
------------------------------------------------------------
 DB Inserted   : 45 (1 in-batch duplicate customer_id collapsed to winner)
 DB Updated    : 0
 DB Skipped    : 0
============================================================

============================================================
 PIPELINE RUN METRICS SUMMARY: run_orders_49fd1641
============================================================
 Dataset       : orders
 Source File   : orders_sample.csv
 Status        : PARTIAL_SUCCESS
 Elapsed (sec) : 0.0399 s
 Throughput    : 2,506.27 records/sec
------------------------------------------------------------
 Input Rows    : 100
 Valid Rows    : 95
 Rejected Rows : 5 (1 malformed, 1 negative amount, 1 bad status, 1 orphan FK, 1 bad date)
------------------------------------------------------------
 DB Inserted   : 94 (1 in-batch duplicate order_id collapsed to winner)
 DB Updated    : 0
 DB Skipped    : 0
============================================================

=================================================================
 GLOBAL DATABASE AUDIT STATUS: PASSED
=================================================================
  [PASS] CUSTOMERS_PK_UNIQUENESS         : Every customer record must have a unique customer_id
  [PASS] ORDERS_PK_UNIQUENESS            : Every order record must have a unique order_id
  [PASS] ORDERS_FK_REFERENTIAL_INTEGRITY : Zero orders may exist referencing non-existent customer_ids
  [PASS] ORDERS_AMOUNT_NON_NEGATIVE      : Zero orders may have negative amount
  [PASS] ORDERS_STATUS_DOMAIN_INTEGRITY  : All order statuses must belong to the documented allowed set
  [PASS] ORDERS_AMOUNT_CENTS_DERIVATION  : Derived amount_cents must exactly match integer conversion of amount * 100
=================================================================
```

### 3. Actual Replay / Idempotency Evidence
On re-running the exact same sample files:
- **Customers**: `DB Inserted: 0`, `DB Updated: 0`, `DB Skipped: 45`
- **Orders**: `DB Inserted: 0`, `DB Updated: 0`, `DB Skipped: 94`
- Total rows in `customers` remains exactly **45**.
- Total rows in `orders` remains exactly **94**.
- Zero unintended duplicate records created.

---

## Performance Benchmarks & Optimizations

To evaluate scalability, a high-scale benchmark was executed using `python -m src.cli benchmark --records 100000`.

### Benchmark Results (200,000 Records Total)
- **Environment**: Python 3.13.14 on Windows 11 (AMD/Intel multi-core CPU, NVMe SSD)
- **Total Records Processed**: 200,000 (100,000 customers + 100,000 orders)
- **Synthetic Data Generation Time**: 2.03 seconds
- **Total Pipeline Execution Time**: **11.95 seconds**
- **Overall Pipeline Throughput**: **16,730.41 records/second**
  - Customers Pipeline: 4.78 seconds (20,902.14 records/second)
  - Orders Pipeline: 6.69 seconds (14,947.68 records/second)
- **Database Consistency Audit**: **100% PASSED** (0 orphan orders, 0 constraint violations).

### Empirical Optimization Comparison
To prove the impact of batching and write-ahead logging over naive row-by-row inserts, both implementations were timed on the exact same dataset:

| Strategy | Records | Elapsed Time | Throughput | Speedup |
|:---|:---:|:---:|:---:|:---:|
| **Naive Row-by-Row (Individual autocommits)** | 500 rows | 3.6019 s | 138.8 rec/s | 1.0x (Baseline) |
| **Optimized Pipeline (Staging Merge + WAL + executemany)** | 500 rows | 0.0190 s | 26,274.2 rec/s | **189.3x faster** |

---

## How to Inspect SQLite Database Directly

You can inspect the generated database `output/pipeline.db` using the Python interactive shell or the SQLite command line:

```powershell
python -c "import sqlite3; conn = sqlite3.connect('output/pipeline.db'); print('Customers:', conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0]); print('Orders:', conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0]); print('Quarantined:', conn.execute('SELECT COUNT(*) FROM quarantine_records').fetchone()[0]);"
```

Useful SQL queries:
```sql
-- 1. Check all recorded pipeline runs
SELECT run_id, dataset_name, status, input_count, valid_count, rejected_count, inserted_count, skipped_count, throughput_records_per_sec 
FROM pipeline_runs;

-- 2. Inspect quarantine audit trail
SELECT rejection_stage, rejection_code, COUNT(*), rejection_reason 
FROM quarantine_records 
GROUP BY rejection_stage, rejection_code;

-- 3. Verify customer spending in cents
SELECT c.customer_id, c.customer_name, COUNT(o.order_id) AS total_orders, SUM(o.amount_cents) / 100.0 AS total_spent_usd
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
GROUP BY c.customer_id
ORDER BY total_spent_usd DESC
LIMIT 5;
```

---

## Known Limitations & Production Roadmap

1. **Database Backend**: SQLite is optimal for single-node embedded systems and coding challenges. For enterprise distributed environments, `SQLiteLoader` can be adapted to PostgreSQL, Snowflake, or Google BigQuery.
2. **Referential Key Scalability**: Currently, valid `customer_id` keys are cached in memory during orders validation. For datasets exceeding 50 million customers, this should be transitioned to a Redis Bloom filter or a database semi-join query.
3. **Orchestration**: The pipeline is designed to be easily scheduled inside Apache Airflow, Prefect, or Dagster, or triggered event-driven via cloud storage file drop events.

---

## Candidate Note & Interview Preparedness

This submission demonstrates engineering judgment, data governance, mathematical rigor, and clarity of explanation. Complete design trade-offs are available in [`DESIGN_NOTES.md`](file:///c:/Users/Gandham%20Anuhya/OneDrive/Desktop/YottaFlex-Data-Pipeline/DESIGN_NOTES.md), data specifications in [`DATA_DICTIONARY.md`](file:///c:/Users/Gandham%20Anuhya/OneDrive/Desktop/YottaFlex-Data-Pipeline/DATA_DICTIONARY.md), and AI transparency logs in [`AI_PROMPT_HISTORY.md`](file:///c:/Users/Gandham%20Anuhya/OneDrive/Desktop/YottaFlex-Data-Pipeline/AI_PROMPT_HISTORY.md).
