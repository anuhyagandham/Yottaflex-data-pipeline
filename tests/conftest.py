"""Pytest shared fixtures: temporary databases, test directories, and sample data generators."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Generator, List

import pytest

from src.config import CUSTOMERS_CONFIG, ORDERS_CONFIG
from src.load import SQLiteLoader


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Provides an isolated temporary directory for test file generation."""
    return tmp_path


@pytest.fixture
def temp_db(temp_dir: Path) -> Path:
    """Provides a fresh isolated SQLite database file."""
    db_file = temp_dir / "test_pipeline.db"
    loader = SQLiteLoader(db_file)
    loader.init_database()
    return db_file


@pytest.fixture
def sample_customers_csv(temp_dir: Path) -> Path:
    """Creates a small valid customers CSV file."""
    csv_file = temp_dir / "valid_customers.csv"
    rows = [
        ["customer_id", "customer_name", "email", "city", "created_at"],
        ["CUST-001", "Alice Smith", "alice@example.com", "Seattle", "2026-01-01T10:00:00Z"],
        ["CUST-002", "Bob Jones", "bob@example.com", "Austin", "2026-01-02T11:00:00Z"],
        ["CUST-003", "Charlie Brown", "charlie@example.com", "New York", "2026-01-03T12:00:00Z"],
    ]
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    return csv_file


@pytest.fixture
def sample_orders_csv(temp_dir: Path) -> Path:
    """Creates a small valid orders CSV referencing customers CUST-001 and CUST-002."""
    csv_file = temp_dir / "valid_orders.csv"
    rows = [
        ["order_id", "customer_id", "order_date", "amount", "status", "updated_at"],
        ["ORD-101", "CUST-001", "2026-02-01", "99.50", "COMPLETED", "2026-02-01T15:00:00Z"],
        ["ORD-102", "CUST-002", "2026-02-02", "149.00", "PENDING", "2026-02-02T16:00:00Z"],
        ["ORD-103", "CUST-001", "2026-02-03", "45.25", "PROCESSING", "2026-02-03T17:00:00Z"],
    ]
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    return csv_file
