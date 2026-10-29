"""Tests for automated reconciliation arithmetic and constraint auditing."""

from __future__ import annotations

from pathlib import Path
import pytest

from src.config import ALLOWED_ORDER_STATUSES, CUSTOMERS_CONFIG, ORDERS_CONFIG
from src.load import SQLiteLoader
from src.metrics import PipelineMetrics
from src.reconcile import ReconciliationEngine


def test_reconciliation_engine_run_invariants(temp_db: Path) -> None:
    loader = SQLiteLoader(temp_db)
    engine = ReconciliationEngine()

    metrics = PipelineMetrics(
        run_id="test_run_rec",
        dataset_name="customers",
        source_file="test.csv",
        input_count=100,
        valid_count=90,
        rejected_count=10,
        inserted_count=80,
        updated_count=5,
        skipped_count=3,
    )
    # in_batch_duplicates = 2 (80 + 5 + 3 + 2 = 90 valid rows)
    in_batch_dups = 2

    # Simulate persisting 10 quarantine records in DB
    with loader.get_connection() as conn:
        for i in range(10):
            conn.execute(
                """INSERT INTO quarantine_records (run_id, dataset_name, source_file, raw_payload, rejection_stage, rejection_code, rejection_reason, created_at)
                   VALUES ('test_run_rec', 'customers', 'test.csv', '{}', 'STAGE', 'CODE', 'REASON', '2026-01-01');"""
            )

        res = engine.reconcile_run(metrics, conn, in_batch_duplicates=in_batch_dups)

    assert res.all_passed is True
    assert len(res.assertions) == 3


def test_reconciliation_detects_mismatched_arithmetic(temp_db: Path) -> None:
    loader = SQLiteLoader(temp_db)
    engine = ReconciliationEngine()

    # Broken arithmetic: input=100, but valid(80) + rejected(10) = 90 != 100
    broken_metrics = PipelineMetrics(
        run_id="broken_run",
        dataset_name="customers",
        source_file="test.csv",
        input_count=100,
        valid_count=80,
        rejected_count=10,
        inserted_count=80,
        updated_count=0,
        skipped_count=0,
    )

    with loader.get_connection() as conn:
        res = engine.reconcile_run(broken_metrics, conn, in_batch_duplicates=0)

    assert res.all_passed is False
    assert res.assertions[0].passed is False
    assert res.assertions[0].name == "RECORD_CONSERVATION_LAW"


def test_database_audit_detects_orphan_and_constraint_violations(temp_db: Path) -> None:
    loader = SQLiteLoader(temp_db)
    engine = ReconciliationEngine()

    with loader.get_connection() as conn:
        # Seed customer
        conn.execute("INSERT INTO customers (customer_id, customer_name, email, created_at, _ingested_at, _source_file, _version) VALUES ('C-1', 'A', 'a@a.com', '2026-01-01', '2026-01-01', 's.csv', 1);")
        # Seed valid order
        conn.execute("INSERT INTO orders (order_id, customer_id, order_date, amount, amount_cents, status, updated_at, _ingested_at, _source_file, _version) VALUES ('O-1', 'C-1', '2026-01-01', 10.0, 1000, 'COMPLETED', '2026-01-01', '2026-01-01', 's.csv', 1);")

        clean_res = engine.reconcile_database(conn)
        assert clean_res.all_passed is True

        # Now insert an orphan order (temporarily disable foreign keys for test simulation)
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("INSERT INTO orders (order_id, customer_id, order_date, amount, amount_cents, status, updated_at, _ingested_at, _source_file, _version) VALUES ('O-ORPHAN', 'C-NONEXISTENT', '2026-01-01', 10.0, 1000, 'COMPLETED', '2026-01-01', '2026-01-01', 's.csv', 1);")
        conn.execute("PRAGMA foreign_keys = ON;")

        dirty_res = engine.reconcile_database(conn)
        assert dirty_res.all_passed is False
        orphan_assertion = [a for a in dirty_res.assertions if a.name == "ORDERS_FK_REFERENTIAL_INTEGRITY"][0]
        assert orphan_assertion.passed is False
        assert orphan_assertion.actual == 1
