"""Configuration schemas and dataset definitions for the YottaFlex Data Pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


# Standard ISO-8601 Regex supporting:
# YYYY-MM-DD, YYYY-MM-DDTHH:MM:SS, YYYY-MM-DDTHH:MM:SSZ, YYYY-MM-DDTHH:MM:SS+HH:MM
ISO8601_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)

# Robust standard email regex (RFC 5322 compatible simplification)
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
)

ALLOWED_ORDER_STATUSES: Set[str] = {
    "PENDING",
    "PROCESSING",
    "COMPLETED",
    "CANCELLED",
    "REFUNDED",
}


@dataclass(frozen=True)
class FieldRule:
    """Validation and type specification for an individual dataset field."""

    name: str
    data_type: str  # 'string', 'int', 'float', 'datetime', 'date'
    required: bool = True  # Must be present in file header
    nullable: bool = False  # Value can be empty or null
    allowed_values: Optional[Set[str]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    regex_pattern: Optional[re.Pattern] = None
    description: str = ""


@dataclass(frozen=True)
class DatasetConfig:
    """Complete specification for a dataset in the pipeline."""

    name: str
    table_name: str
    primary_key: str
    version_col: str  # Used for deterministic upsert / conflict resolution
    fields: Dict[str, FieldRule]
    foreign_keys: Dict[str, str] = field(default_factory=dict)
    ddl: str = ""
    allow_extra_columns: bool = False

    @property
    def required_columns(self) -> List[str]:
        return [f.name for f in self.fields.values() if f.required]

    @property
    def all_column_names(self) -> List[str]:
        return list(self.fields.keys())


# -----------------------------------------------------------------------------
# Database DDLs
# -----------------------------------------------------------------------------

PIPELINE_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    dataset_name TEXT NOT NULL,
    source_file TEXT NOT NULL,
    status TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    input_count INTEGER NOT NULL DEFAULT 0,
    valid_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds REAL NOT NULL DEFAULT 0.0,
    throughput_records_per_sec REAL NOT NULL DEFAULT 0.0,
    error_message TEXT,
    created_at TEXT NOT NULL
);
"""

QUARANTINE_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS quarantine_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_row_number INTEGER,
    raw_payload TEXT NOT NULL,
    rejection_stage TEXT NOT NULL,
    rejection_code TEXT NOT NULL,
    rejection_reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quarantine_run_id ON quarantine_records(run_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_dataset ON quarantine_records(dataset_name);
"""

CUSTOMERS_DDL = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    email TEXT NOT NULL,
    city TEXT,
    created_at TEXT NOT NULL,
    _ingested_at TEXT NOT NULL,
    _source_file TEXT NOT NULL,
    _version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
"""

ORDERS_DDL = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    order_date TEXT NOT NULL,
    amount REAL NOT NULL CHECK(amount >= 0),
    amount_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    _ingested_at TEXT NOT NULL,
    _source_file TEXT NOT NULL,
    _version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
"""


# -----------------------------------------------------------------------------
# Dataset Configurations
# -----------------------------------------------------------------------------

CUSTOMERS_CONFIG = DatasetConfig(
    name="customers",
    table_name="customers",
    primary_key="customer_id",
    version_col="created_at",
    ddl=CUSTOMERS_DDL,
    fields={
        "customer_id": FieldRule(
            name="customer_id",
            data_type="string",
            required=True,
            nullable=False,
            description="Stable unique customer identifier",
        ),
        "customer_name": FieldRule(
            name="customer_name",
            data_type="string",
            required=True,
            nullable=False,
            description="Full name of customer",
        ),
        "email": FieldRule(
            name="email",
            data_type="string",
            required=True,
            nullable=False,
            regex_pattern=EMAIL_REGEX,
            description="Normalized email address",
        ),
        "city": FieldRule(
            name="city",
            data_type="string",
            required=False,
            nullable=True,
            description="Customer city location (nullable)",
        ),
        "created_at": FieldRule(
            name="created_at",
            data_type="datetime",
            required=True,
            nullable=False,
            regex_pattern=ISO8601_REGEX,
            description="Timestamp when customer was created (ISO-8601)",
        ),
    },
)

ORDERS_CONFIG = DatasetConfig(
    name="orders",
    table_name="orders",
    primary_key="order_id",
    version_col="updated_at",
    ddl=ORDERS_DDL,
    foreign_keys={"customer_id": "customers.customer_id"},
    fields={
        "order_id": FieldRule(
            name="order_id",
            data_type="string",
            required=True,
            nullable=False,
            description="Stable unique order identifier",
        ),
        "customer_id": FieldRule(
            name="customer_id",
            data_type="string",
            required=True,
            nullable=False,
            description="Foreign key referencing customers.customer_id",
        ),
        "order_date": FieldRule(
            name="order_date",
            data_type="date",
            required=True,
            nullable=False,
            regex_pattern=ISO8601_REGEX,
            description="Date order was placed (ISO-8601 YYYY-MM-DD or timestamp)",
        ),
        "amount": FieldRule(
            name="amount",
            data_type="float",
            required=True,
            nullable=False,
            min_value=0.0,
            description="Order total amount in USD (non-negative, finite)",
        ),
        "status": FieldRule(
            name="status",
            data_type="string",
            required=True,
            nullable=False,
            allowed_values=ALLOWED_ORDER_STATUSES,
            description="Order lifecycle status",
        ),
        "updated_at": FieldRule(
            name="updated_at",
            data_type="datetime",
            required=True,
            nullable=False,
            regex_pattern=ISO8601_REGEX,
            description="Timestamp of last order update (ISO-8601)",
        ),
    },
)

DATASET_CONFIGS: Dict[str, DatasetConfig] = {
    "customers": CUSTOMERS_CONFIG,
    "orders": ORDERS_CONFIG,
}
