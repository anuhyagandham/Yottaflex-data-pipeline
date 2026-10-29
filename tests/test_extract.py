"""Tests for CSV extraction, malformed row quarantine, and schema drift detection."""

from __future__ import annotations

import csv
from pathlib import Path
import pytest

from src.config import CUSTOMERS_CONFIG
from src.extract import CSVExtractor
from src.quarantine import QuarantineManager


def test_extract_happy_path(temp_dir: Path, sample_customers_csv: Path) -> None:
    extractor = CSVExtractor(CUSTOMERS_CONFIG)
    q_mgr = QuarantineManager()
    result = extractor.extract(sample_customers_csv, run_id="test_run", quarantine_mgr=q_mgr)

    assert len(result.records) == 3
    assert q_mgr.count == 0
    assert result.drift_detected is False
    assert result.records[0].data["customer_id"] == "CUST-001"
    assert result.records[0].row_number == 2


def test_extract_empty_file(temp_dir: Path) -> None:
    empty_file = temp_dir / "empty.csv"
    empty_file.touch()

    extractor = CSVExtractor(CUSTOMERS_CONFIG)
    q_mgr = QuarantineManager()
    result = extractor.extract(empty_file, run_id="test_run", quarantine_mgr=q_mgr)

    assert len(result.records) == 0
    assert q_mgr.count == 0
    assert result.total_raw_rows == 0


def test_extract_header_only(temp_dir: Path) -> None:
    header_file = temp_dir / "header_only.csv"
    header_file.write_text("customer_id,customer_name,email,city,created_at\n", encoding="utf-8")

    extractor = CSVExtractor(CUSTOMERS_CONFIG)
    q_mgr = QuarantineManager()
    result = extractor.extract(header_file, run_id="test_run", quarantine_mgr=q_mgr)

    assert len(result.records) == 0
    assert q_mgr.count == 0


def test_extract_malformed_rows_quarantined(temp_dir: Path) -> None:
    malformed_csv = temp_dir / "malformed.csv"
    content = (
        "customer_id,customer_name,email,city,created_at\n"
        "CUST-001,Alice Smith,alice@example.com,Seattle,2026-01-01T10:00:00Z\n"
        "CUST-002,Bob Jones,bob@example.com\n"  # Too few fields (3 instead of 5)
        "CUST-003,Charlie,charlie@example.com,NYC,2026-01-03,EXTRA1,EXTRA2\n"  # Too many fields (7 instead of 5)
        "CUST-004,David,david@example.com,Austin,2026-01-04T10:00:00Z\n"
    )
    malformed_csv.write_text(content, encoding="utf-8")

    extractor = CSVExtractor(CUSTOMERS_CONFIG)
    q_mgr = QuarantineManager()
    result = extractor.extract(malformed_csv, run_id="test_run", quarantine_mgr=q_mgr)

    # 2 valid records, 2 quarantined
    assert len(result.records) == 2
    assert q_mgr.count == 2
    assert q_mgr.records[0].source_row_number == 3
    assert q_mgr.records[0].rejection_code == "MALFORMED_CSV_ROW"
    assert q_mgr.records[1].source_row_number == 4
    assert q_mgr.records[1].rejection_code == "MALFORMED_CSV_ROW"


def test_extract_schema_drift_extra_columns(temp_dir: Path) -> None:
    drift_csv = temp_dir / "drift.csv"
    content = (
        "customer_id,customer_name,email,city,created_at,loyalty_tier,referral_code\n"
        "CUST-001,Alice Smith,alice@example.com,Seattle,2026-01-01T10:00:00Z,GOLD,REF123\n"
    )
    drift_csv.write_text(content, encoding="utf-8")

    extractor = CSVExtractor(CUSTOMERS_CONFIG)
    result = extractor.extract(drift_csv, run_id="test_run")

    assert result.drift_detected is True
    assert set(result.extra_columns) == {"loyalty_tier", "referral_code"}
    assert len(result.records) == 1
    assert result.records[0].data["loyalty_tier"] == "GOLD"


def test_extract_missing_columns_detected(temp_dir: Path) -> None:
    missing_csv = temp_dir / "missing_cols.csv"
    content = "customer_id,customer_name\nCUST-001,Alice\n"
    missing_csv.write_text(content, encoding="utf-8")

    extractor = CSVExtractor(CUSTOMERS_CONFIG)
    result = extractor.extract(missing_csv, run_id="test_run")

    assert set(result.missing_columns) == {"email", "created_at"}


def test_extract_utf8_bom_handling(temp_dir: Path) -> None:
    bom_file = temp_dir / "bom.csv"
    content = "customer_id,customer_name,email,city,created_at\nCUST-001,Alice,alice@example.com,NYC,2026-01-01T00:00:00Z\n"
    # Write with utf-8-sig (adds BOM)
    bom_file.write_text(content, encoding="utf-8-sig")

    extractor = CSVExtractor(CUSTOMERS_CONFIG)
    result = extractor.extract(bom_file, run_id="test_run")

    assert result.headers[0] == "customer_id"  # No BOM prefix like '\ufeffcustomer_id'
    assert len(result.records) == 1


def test_extract_non_existent_file() -> None:
    extractor = CSVExtractor(CUSTOMERS_CONFIG)
    with pytest.raises(FileNotFoundError):
        extractor.extract("non_existent_file_path_12345.csv", run_id="test_run")
