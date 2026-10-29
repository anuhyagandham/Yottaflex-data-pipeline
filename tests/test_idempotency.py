"""Tests verifying idempotency, replay safety, and deterministic backfilling."""

from __future__ import annotations

import csv
from pathlib import Path
import pytest

from src.config import CUSTOMERS_CONFIG, ORDERS_CONFIG
from src.pipeline import Pipeline


def test_idempotent_three_runs_produce_stable_database_state(
    temp_db: Path, sample_customers_csv: Path
) -> None:
    pipeline = Pipeline(CUSTOMERS_CONFIG, temp_db)

    # Run 1: initial ingestion
    m1, r1 = pipeline.run(sample_customers_csv, custom_run_id="run_1")
    assert m1.inserted_count == 3
    assert m1.updated_count == 0
    assert m1.skipped_count == 0

    # Run 2: exact replay of the same file
    m2, r2 = pipeline.run(sample_customers_csv, custom_run_id="run_2")
    assert m2.inserted_count == 0
    assert m2.updated_count == 0
    assert m2.skipped_count == 3

    # Run 3: third replay of the same file
    m3, r3 = pipeline.run(sample_customers_csv, custom_run_id="run_3")
    assert m3.inserted_count == 0
    assert m3.updated_count == 0
    assert m3.skipped_count == 3

    # Ensure total rows in database remains exactly 3
    with pipeline.loader.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM customers;").fetchone()[0]
        assert count == 3
        distinct_count = conn.execute("SELECT COUNT(DISTINCT customer_id) FROM customers;").fetchone()[0]
        assert distinct_count == 3


def test_incremental_batch_with_partial_overlap(temp_db: Path, temp_dir: Path) -> None:
    pipeline = Pipeline(CUSTOMERS_CONFIG, temp_db)

    # Batch 1: Customers A, B
    b1_file = temp_dir / "batch_1.csv"
    with open(b1_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["customer_id", "customer_name", "email", "city", "created_at"])
        writer.writerow(["C-01", "Alice", "alice@ex.com", "Seattle", "2026-01-01T10:00:00Z"])
        writer.writerow(["C-02", "Bob", "bob@ex.com", "Austin", "2026-01-01T10:00:00Z"])

    m1, _ = pipeline.run(b1_file, custom_run_id="batch_1")
    assert m1.inserted_count == 2
    assert m1.updated_count == 0
    assert m1.skipped_count == 0

    # Batch 2:
    # - C-01: newer version (updated name, created_at is newer)
    # - C-02: identical version (should skip)
    # - C-03: new customer (should insert)
    b2_file = temp_dir / "batch_2.csv"
    with open(b2_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["customer_id", "customer_name", "email", "city", "created_at"])
        writer.writerow(["C-01", "Alice Updated", "alice.new@ex.com", "Portland", "2026-01-02T10:00:00Z"])
        writer.writerow(["C-02", "Bob", "bob@ex.com", "Austin", "2026-01-01T10:00:00Z"])
        writer.writerow(["C-03", "Charlie", "charlie@ex.com", "Denver", "2026-01-02T10:00:00Z"])

    m2, r2 = pipeline.run(b2_file, custom_run_id="batch_2")
    assert m2.inserted_count == 1  # C-03
    assert m2.updated_count == 1   # C-01
    assert m2.skipped_count == 1   # C-02
    assert r2.all_passed is True

    # Database state verification
    with pipeline.loader.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM customers;").fetchone()[0]
        assert count == 3
        c1_row = conn.execute("SELECT customer_name, city, _version FROM customers WHERE customer_id = 'C-01';").fetchone()
        assert c1_row[0] == "Alice Updated"
        assert c1_row[1] == "Portland"
        assert c1_row[2] == 2
