"""Tests for end-to-end pipeline execution and multi-dataset dependency coordination."""

from __future__ import annotations

import csv
from pathlib import Path
import pytest

from src.config import CUSTOMERS_CONFIG, ORDERS_CONFIG
from src.load import SQLiteLoader
from src.pipeline import Pipeline, run_pipeline_suite


def test_customers_pipeline_end_to_end(temp_db: Path, sample_customers_csv: Path) -> None:
    pipeline = Pipeline(CUSTOMERS_CONFIG, temp_db)
    metrics, reconcile_res = pipeline.run(sample_customers_csv)

    assert metrics.status == "SUCCESS"
    assert metrics.input_count == 3
    assert metrics.valid_count == 3
    assert metrics.rejected_count == 0
    assert metrics.inserted_count == 3
    assert reconcile_res.all_passed is True

    # Verify rows in DB
    with pipeline.loader.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM customers;").fetchone()[0]
        assert count == 3
        # Check run history
        run_row = conn.execute("SELECT status, input_count FROM pipeline_runs WHERE run_id = ?;", (metrics.run_id,)).fetchone()
        assert run_row[0] == "SUCCESS"
        assert run_row[1] == 3


def test_orders_pipeline_with_fk_quarantine(temp_db: Path, temp_dir: Path) -> None:
    # 1. Seed customer CUST-001 into DB
    with SQLiteLoader(temp_db).get_connection() as conn:
        conn.execute(
            """INSERT INTO customers (customer_id, customer_name, email, city, created_at, _ingested_at, _source_file, _version)
               VALUES ('CUST-001', 'Alice', 'alice@ex.com', 'Seattle', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'seed.csv', 1);"""
        )

    # 2. Orders CSV with 1 valid order and 1 orphan order
    orders_csv = temp_dir / "mixed_orders.csv"
    with open(orders_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["order_id", "customer_id", "order_date", "amount", "status", "updated_at"])
        writer.writerow(["ORD-01", "CUST-001", "2026-02-01", "100.00", "COMPLETED", "2026-02-01T10:00:00Z"])
        writer.writerow(["ORD-02", "CUST-ORPHAN-999", "2026-02-01", "50.00", "PENDING", "2026-02-01T10:00:00Z"])

    pipeline = Pipeline(ORDERS_CONFIG, temp_db)
    metrics, reconcile_res = pipeline.run(orders_csv)

    assert metrics.status == "PARTIAL_SUCCESS"
    assert metrics.input_count == 2
    assert metrics.valid_count == 1
    assert metrics.rejected_count == 1
    assert metrics.inserted_count == 1
    assert reconcile_res.all_passed is True

    # Verify quarantine table has the orphan order
    with pipeline.loader.get_connection() as conn:
        q_row = conn.execute("SELECT rejection_code, rejection_stage FROM quarantine_records WHERE run_id = ?;", (metrics.run_id,)).fetchone()
        assert q_row[0] == "ORPHAN_FOREIGN_KEY"
        assert q_row[1] == "BUSINESS_VALIDATION"


def test_run_pipeline_suite_dependency_order(temp_db: Path, sample_customers_csv: Path, sample_orders_csv: Path) -> None:
    results = run_pipeline_suite(temp_db, sample_customers_csv, sample_orders_csv)

    c_met = results["customers_metrics"]
    o_met = results["orders_metrics"]
    db_audit = results["database_audit"]

    assert c_met.status == "SUCCESS"
    assert o_met.status == "SUCCESS"
    assert db_audit.all_passed is True
    assert db_audit.customers_count == 3
    assert db_audit.orders_count == 3


def test_pipeline_empty_file_handling(temp_db: Path, temp_dir: Path) -> None:
    empty_csv = temp_dir / "empty.csv"
    empty_csv.touch()

    pipeline = Pipeline(CUSTOMERS_CONFIG, temp_db)
    metrics, reconcile_res = pipeline.run(empty_csv)

    assert metrics.status == "SUCCESS"
    assert metrics.input_count == 0
    assert metrics.valid_count == 0
    assert metrics.inserted_count == 0
    assert reconcile_res.all_passed is True
