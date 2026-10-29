"""Tests for SQLite loading, idempotent upserts, conflict resolution, rollback, and retries."""

from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from src.config import ORDERS_CONFIG
from src.load import SQLiteLoader, with_retry


def seed_customer(loader: SQLiteLoader, customer_id: str = "CUST-001") -> None:
    with loader.get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO customers (customer_id, customer_name, email, city, created_at, _ingested_at, _source_file, _version)
               VALUES (?, 'Seed User', 'seed@ex.com', 'Seattle', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'seed.csv', 1);""",
            (customer_id,),
        )


def test_load_initial_and_repeat_idempotency(temp_db: Path) -> None:
    loader = SQLiteLoader(temp_db)
    seed_customer(loader, "CUST-001")

    # First run: 2 records
    records = [
        {
            "order_id": "ORD-001",
            "customer_id": "CUST-001",
            "order_date": "2026-02-01",
            "amount": 100.0,
            "amount_cents": 10000,
            "status": "PENDING",
            "updated_at": "2026-02-01T10:00:00Z",
            "_ingested_at": "2026-02-01T11:00:00Z",
            "_source_file": "orders1.csv",
            "_version": 1,
        },
        {
            "order_id": "ORD-002",
            "customer_id": "CUST-001",
            "order_date": "2026-02-01",
            "amount": 50.0,
            "amount_cents": 5000,
            "status": "COMPLETED",
            "updated_at": "2026-02-01T10:00:00Z",
            "_ingested_at": "2026-02-01T11:00:00Z",
            "_source_file": "orders1.csv",
            "_version": 1,
        },
    ]

    # Initial load: both are inserted
    res1 = loader.load_dataset(ORDERS_CONFIG, records)
    assert res1.inserted_count == 2
    assert res1.updated_count == 0
    assert res1.skipped_count == 0

    # Repeat load with exact same data: both must be skipped (Idempotent!)
    res2 = loader.load_dataset(ORDERS_CONFIG, records)
    assert res2.inserted_count == 0
    assert res2.updated_count == 0
    assert res2.skipped_count == 2

    # Verify count in database
    with loader.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM orders;").fetchone()[0]
        assert count == 2


def test_load_newer_version_updates_and_older_version_skips(temp_db: Path) -> None:
    loader = SQLiteLoader(temp_db)
    seed_customer(loader, "CUST-001")

    initial_record = [
        {
            "order_id": "ORD-001",
            "customer_id": "CUST-001",
            "order_date": "2026-02-01",
            "amount": 100.0,
            "amount_cents": 10000,
            "status": "PENDING",
            "updated_at": "2026-02-01T10:00:00Z",
            "_ingested_at": "2026-02-01T11:00:00Z",
            "_source_file": "initial.csv",
            "_version": 1,
        }
    ]
    loader.load_dataset(ORDERS_CONFIG, initial_record)

    # 1. Update with NEWER timestamp -> should update and increment version
    newer_record = [
        {
            "order_id": "ORD-001",
            "customer_id": "CUST-001",
            "order_date": "2026-02-01",
            "amount": 120.0,
            "amount_cents": 12000,
            "status": "COMPLETED",
            "updated_at": "2026-02-01T12:00:00Z",  # Later than 10:00:00Z
            "_ingested_at": "2026-02-01T12:30:00Z",
            "_source_file": "update.csv",
            "_version": 1,
        }
    ]
    res_update = loader.load_dataset(ORDERS_CONFIG, newer_record)
    assert res_update.inserted_count == 0
    assert res_update.updated_count == 1
    assert res_update.skipped_count == 0

    with loader.get_connection() as conn:
        row = conn.execute("SELECT amount, status, _version FROM orders WHERE order_id = 'ORD-001';").fetchone()
        assert row[0] == 120.0
        assert row[1] == "COMPLETED"
        assert row[2] == 2  # Version incremented to 2

    # 2. Out-of-order update with OLDER timestamp -> should SKIP (no regression)
    older_record = [
        {
            "order_id": "ORD-001",
            "customer_id": "CUST-001",
            "order_date": "2026-02-01",
            "amount": 80.0,
            "amount_cents": 8000,
            "status": "PENDING",
            "updated_at": "2026-02-01T08:00:00Z",  # Older than current 12:00:00Z
            "_ingested_at": "2026-02-01T13:00:00Z",
            "_source_file": "late_arrival.csv",
            "_version": 1,
        }
    ]
    res_skip = loader.load_dataset(ORDERS_CONFIG, older_record)
    assert res_skip.inserted_count == 0
    assert res_skip.updated_count == 0
    assert res_skip.skipped_count == 1

    # Database still preserves the newer state
    with loader.get_connection() as conn:
        row = conn.execute("SELECT amount, status, _version FROM orders WHERE order_id = 'ORD-001';").fetchone()
        assert row[0] == 120.0
        assert row[1] == "COMPLETED"
        assert row[2] == 2


def test_transaction_rollback_on_failure(temp_db: Path) -> None:
    loader = SQLiteLoader(temp_db)
    seed_customer(loader, "C1")

    # Initial state: 1 order
    initial = [
        {
            "order_id": "ORD-INIT",
            "customer_id": "C1",
            "order_date": "2026-02-01",
            "amount": 50.0,
            "amount_cents": 5000,
            "status": "PENDING",
            "updated_at": "2026-02-01T10:00:00Z",
            "_ingested_at": "2026-02-01T10:00:00Z",
            "_source_file": "init.csv",
            "_version": 1,
        }
    ]
    loader.load_dataset(ORDERS_CONFIG, initial)

    # Now attempt a batch containing an unhandled schema error (e.g. invalid column structure)
    bad_batch = [
        {
            "order_id": "ORD-GOOD",
            "customer_id": "C1",
            "order_date": "2026-02-01",
            "amount": 75.0,
            "amount_cents": 7500,
            "status": "PENDING",
            "updated_at": "2026-02-01T10:00:00Z",
            "_ingested_at": "2026-02-01T10:00:00Z",
            "_source_file": "bad.csv",
            "_version": 1,
            "NON_EXISTENT_COLUMN": "FAIL_ME",  # Will cause SQLite error on insert
        }
    ]

    with pytest.raises(Exception):
        loader.load_dataset(ORDERS_CONFIG, bad_batch)

    # Verify that transaction rolled back: ORD-GOOD was NOT committed
    with loader.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM orders;").fetchone()[0]
        assert count == 1
        row = conn.execute("SELECT order_id FROM orders;").fetchone()
        assert row[0] == "ORD-INIT"


def test_with_retry_decorator_succeeds_after_transient_error() -> None:
    calls = 0

    @with_retry(max_retries=3, base_delay=0.01)
    def flaky_db_call() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise sqlite3.OperationalError("database is locked")
        return "SUCCESS"

    result = flaky_db_call()
    assert result == "SUCCESS"
    assert calls == 3
