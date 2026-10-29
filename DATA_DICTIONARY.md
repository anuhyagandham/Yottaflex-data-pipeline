# Data Dictionary

This document details the schema specifications, data types, nullability, keys, constraints, business validation rules, and derived attributes for the **YottaFlex Reusable Data Pipeline Framework**.

---

## 1. Customers Dataset

### Grain & Entities
- **Grain**: One row represents a single registered customer account.
- **Natural / Primary Key**: `customer_id`
- **Dependencies**: Parent table for `orders`. Must be ingested prior to `orders` to satisfy referential integrity constraints.

### Field Definitions

| Column Name | Ingestion Stage | Storage Type | Nullable | Primary / Foreign Key | Validation Rules & Constraints | Description |
|:---|:---|:---|:---:|:---:|:---|:---|
| `customer_id` | Source & Target | `TEXT` | No | **Primary Key** | Required non-empty string. Unique constraint enforced at database level. Format: `CUST-XXXX`. | Stable unique identifier for the customer. |
| `customer_name` | Source & Target | `TEXT` | No | None | Required non-empty string. Whitespace trimmed during transformation. | Full name of the customer. |
| `email` | Source & Target | `TEXT` | No | Indexed | RFC 5322 compliant email regex: `^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$`. Normalized to lowercase. | Primary contact email address. |
| `city` | Source & Target | `TEXT` | **Yes** | None | Optional nullable string. Trimmed during transform. Converted to SQL `NULL` if blank. | Primary residential/shipping city. |
| `created_at` | Source & Target | `TEXT` | No | Version Key | Valid ISO-8601 UTC timestamp (`YYYY-MM-DDTHH:MM:SSZ` or with offset). Normalized to canonical UTC. | Account creation timestamp. Used as version conflict resolver for customer updates. |
| `loyalty_tier` | Source (Drift) | N/A | Yes | None | Not part of core schema. Detected by schema drift engine and logged as extra column without breaking ingestion. | Example extra attribute demonstrating schema drift tolerance. |
| `_ingested_at` | Derived Target | `TEXT` | No | None | System-generated ISO-8601 UTC timestamp generated during ingestion. | Lineage and ingestion audit timestamp. |
| `_source_file` | Derived Target | `TEXT` | No | None | Basename of the file from which the row was extracted. | Source provenance traceability. |
| `_version` | Derived Target | `INTEGER` | No | None | Monotonically increasing version counter (initial value 1; incremented on subsequent updates). | Optimistic locking and update version tracking. |

---

## 2. Orders Dataset

### Grain & Entities
- **Grain**: One row represents a discrete purchase event / transaction.
- **Natural / Primary Key**: `order_id`
- **Foreign Keys**: `customer_id` referencing `customers(customer_id)` with `ON DELETE RESTRICT`.

### Field Definitions

| Column Name | Ingestion Stage | Storage Type | Nullable | Primary / Foreign Key | Validation Rules & Constraints | Description |
|:---|:---|:---|:---:|:---:|:---|:---|
| `order_id` | Source & Target | `TEXT` | No | **Primary Key** | Required non-empty string. Unique constraint enforced at database level. Format: `ORD-XXXXX`. | Stable unique order identifier. |
| `customer_id` | Source & Target | `TEXT` | No | **Foreign Key** | References `customers(customer_id)`. Must exist in database before ingestion or row is quarantined as `ORPHAN_FOREIGN_KEY`. | Customer who placed the order. |
| `order_date` | Source & Target | `TEXT` | No | None | ISO-8601 calendar date (`YYYY-MM-DD`) or timestamp. Normalized to canonical ISO string. | Date when the purchase was placed. |
| `amount` | Source & Target | `REAL` | No | None | Numeric, finite, non-negative (`amount >= 0.0`). Checked via SQLite table constraint and business validator. | Order monetary total in USD. |
| `amount_cents` | **Derived Target** | `INTEGER` | No | None | Exact integer derived field calculated as `int(round(amount * 100))`. Verified by reconciliation engine. | Order total in cents, avoiding floating-point rounding errors in downstream accounting. |
| `status` | Source & Target | `TEXT` | No | Indexed | Restricted to documented enum set: `PENDING`, `PROCESSING`, `COMPLETED`, `CANCELLED`, `REFUNDED`. Normalized to uppercase. | Lifecycle status of the order. |
| `updated_at` | Source & Target | `TEXT` | No | Version Key | Valid ISO-8601 UTC timestamp. Used for deterministic upsert resolution: newer timestamps overwrite older versions; older timestamps are skipped. | Timestamp of the last status or detail update. |
| `_ingested_at` | Derived Target | `TEXT` | No | None | System-generated ISO-8601 UTC timestamp generated during ingestion. | Lineage and ingestion audit timestamp. |
| `_source_file` | Derived Target | `TEXT` | No | None | Basename of the file from which the row was extracted. | Source provenance traceability. |
| `_version` | Derived Target | `INTEGER` | No | None | Monotonically increasing version counter (initial value 1; incremented on updates). | Optimistic concurrency and mutation history tracking. |

---

## 3. Quarantine Records Table (`quarantine_records`)

| Column Name | Type | Description |
|:---|:---|:---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | Auto-incrementing unique quarantine event identifier. |
| `run_id` | `TEXT NOT NULL` | Identifier of the pipeline execution run during which the record was rejected. |
| `dataset_name` | `TEXT NOT NULL` | Target dataset (`customers` or `orders`). |
| `source_file` | `TEXT NOT NULL` | Name of the source file containing the rejected line. |
| `source_row_number` | `INTEGER` | 1-indexed row number in the source file where the rejection occurred (enables exact debugging). |
| `raw_payload` | `TEXT NOT NULL` | Original row contents formatted as JSON string or raw text. Preserves original data unaltered. |
| `rejection_stage` | `TEXT NOT NULL` | Pipeline stage that caught the defect: `EXTRACT`, `SCHEMA_VALIDATION`, or `BUSINESS_VALIDATION`. |
| `rejection_code` | `TEXT NOT NULL` | Standardized machine-readable error code (e.g., `MALFORMED_CSV_ROW`, `EMPTY_REQUIRED_FIELD`, `INVALID_EMAIL_FORMAT`, `NEGATIVE_NUMERIC_VALUE`, `ORPHAN_FOREIGN_KEY`). |
| `rejection_reason` | `TEXT NOT NULL` | Human-readable explanation of why the record was rejected. |
| `created_at` | `TEXT NOT NULL` | ISO-8601 UTC timestamp when the quarantine entry was generated. |

---

## 4. Pipeline Runs Audit Table (`pipeline_runs`)

| Column Name | Type | Description |
|:---|:---|:---|
| `run_id` | `TEXT PRIMARY KEY` | Globally unique run identifier (`run_<dataset>_<uuid>`). |
| `dataset_name` | `TEXT NOT NULL` | Dataset processed (`customers` or `orders`). |
| `source_file` | `TEXT NOT NULL` | Filename processed. |
| `status` | `TEXT NOT NULL` | Final outcome: `SUCCESS`, `PARTIAL_SUCCESS`, or `FAILED`. |
| `start_time` | `TEXT NOT NULL` | UTC ISO-8601 timestamp at execution start. |
| `end_time` | `TEXT` | UTC ISO-8601 timestamp at execution finish. |
| `input_count` | `INTEGER NOT NULL` | Total raw rows encountered (including header/blank rows and malformed lines). |
| `valid_count` | `INTEGER NOT NULL` | Number of rows that passed schema and business validation. |
| `rejected_count` | `INTEGER NOT NULL` | Number of rows rejected and routed to quarantine. |
| `inserted_count` | `INTEGER NOT NULL` | Number of new records inserted into target table. |
| `updated_count` | `INTEGER NOT NULL` | Number of existing records updated due to newer version timestamp. |
| `skipped_count` | `INTEGER NOT NULL` | Number of records skipped because identical or older version was already present. |
| `elapsed_seconds` | `REAL NOT NULL` | Wall-clock elapsed runtime in seconds. |
| `throughput_records_per_sec` | `REAL NOT NULL` | Measured throughput: `input_count / elapsed_seconds`. |
| `error_message` | `TEXT` | Unhandled error message or exception traceback if status was `FAILED`. |
| `created_at` | `TEXT NOT NULL` | Record creation timestamp. |
