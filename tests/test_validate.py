"""Tests for schema validation and business domain validation."""

from __future__ import annotations

import pytest

from src.config import CUSTOMERS_CONFIG, ORDERS_CONFIG
from src.extract import ExtractedRecord
from src.quarantine import QuarantineManager
from src.validate import BusinessValidator, SchemaValidator


def test_schema_validator_catches_missing_and_empty_fields() -> None:
    validator = SchemaValidator(CUSTOMERS_CONFIG)
    q_mgr = QuarantineManager()

    records = [
        # Valid record
        ExtractedRecord(
            row_number=2,
            data={"customer_id": "CUST-001", "customer_name": "Alice", "email": "alice@ex.com", "city": "NYC", "created_at": "2026-01-01T00:00:00Z"},
            raw_payload="raw1",
        ),
        # Empty non-nullable field (email is empty)
        ExtractedRecord(
            row_number=3,
            data={"customer_id": "CUST-002", "customer_name": "Bob", "email": "", "city": "Austin", "created_at": "2026-01-01T00:00:00Z"},
            raw_payload="raw2",
        ),
        # Missing required field (created_at not in dict)
        ExtractedRecord(
            row_number=4,
            data={"customer_id": "CUST-003", "customer_name": "Charlie", "email": "c@ex.com", "city": "Seattle"},
            raw_payload="raw3",
        ),
    ]

    valid, _ = validator.validate(records, run_id="r1", source_file="f.csv", quarantine_mgr=q_mgr)

    assert len(valid) == 1
    assert valid[0].data["customer_id"] == "CUST-001"
    assert q_mgr.count == 2
    assert q_mgr.records[0].rejection_code == "EMPTY_REQUIRED_FIELD"
    assert q_mgr.records[1].rejection_code == "MISSING_REQUIRED_COLUMN"


def test_schema_validator_catches_invalid_timestamp_and_types() -> None:
    validator = SchemaValidator(ORDERS_CONFIG)
    q_mgr = QuarantineManager()

    records = [
        # Invalid timestamp
        ExtractedRecord(
            row_number=2,
            data={"order_id": "ORD-01", "customer_id": "C1", "order_date": "2026-02-01", "amount": "10.0", "status": "PENDING", "updated_at": "NOT_A_DATE"},
            raw_payload="raw1",
        ),
        # Invalid numeric float
        ExtractedRecord(
            row_number=3,
            data={"order_id": "ORD-02", "customer_id": "C1", "order_date": "2026-02-01", "amount": "TEN_DOLLARS", "status": "PENDING", "updated_at": "2026-02-01T00:00:00Z"},
            raw_payload="raw2",
        ),
        # Non-finite float (NaN or inf)
        ExtractedRecord(
            row_number=4,
            data={"order_id": "ORD-03", "customer_id": "C1", "order_date": "2026-02-01", "amount": "nan", "status": "PENDING", "updated_at": "2026-02-01T00:00:00Z"},
            raw_payload="raw3",
        ),
    ]

    valid, _ = validator.validate(records, run_id="r1", source_file="f.csv", quarantine_mgr=q_mgr)
    assert len(valid) == 0
    assert q_mgr.count == 3
    assert q_mgr.records[0].rejection_code == "INVALID_TIMESTAMP_FORMAT"
    assert q_mgr.records[1].rejection_code == "INVALID_DATA_TYPE"
    assert q_mgr.records[2].rejection_code == "NON_FINITE_NUMERIC"


def test_business_validator_email_rules() -> None:
    validator = BusinessValidator(CUSTOMERS_CONFIG)
    q_mgr = QuarantineManager()

    records = [
        ExtractedRecord(
            row_number=2,
            data={"customer_id": "C1", "customer_name": "Good", "email": "valid.user@example.co.uk", "city": "London", "created_at": "2026-01-01T00:00:00Z"},
            raw_payload="raw1",
        ),
        ExtractedRecord(
            row_number=3,
            data={"customer_id": "C2", "customer_name": "Bad", "email": "missing-at-sign.com", "city": "NYC", "created_at": "2026-01-01T00:00:00Z"},
            raw_payload="raw2",
        ),
    ]

    valid, _ = validator.validate(records, run_id="r1", source_file="f.csv", quarantine_mgr=q_mgr)
    assert len(valid) == 1
    assert valid[0].data["customer_id"] == "C1"
    assert q_mgr.count == 1
    assert q_mgr.records[0].rejection_code == "INVALID_EMAIL_FORMAT"


def test_business_validator_order_rules_and_referential_integrity() -> None:
    validator = BusinessValidator(ORDERS_CONFIG)
    q_mgr = QuarantineManager()

    parent_keys = {"CUST-001", "CUST-002"}

    records = [
        # Valid order
        ExtractedRecord(
            row_number=2,
            data={"order_id": "O1", "customer_id": "CUST-001", "order_date": "2026-02-01", "amount": "50.00", "status": "COMPLETED", "updated_at": "2026-02-01T00:00:00Z"},
            raw_payload="raw1",
        ),
        # Negative amount
        ExtractedRecord(
            row_number=3,
            data={"order_id": "O2", "customer_id": "CUST-001", "order_date": "2026-02-01", "amount": "-15.00", "status": "COMPLETED", "updated_at": "2026-02-01T00:00:00Z"},
            raw_payload="raw2",
        ),
        # Invalid status
        ExtractedRecord(
            row_number=4,
            data={"order_id": "O3", "customer_id": "CUST-001", "order_date": "2026-02-01", "amount": "20.00", "status": "SUSPICIOUS_STATUS", "updated_at": "2026-02-01T00:00:00Z"},
            raw_payload="raw3",
        ),
        # Orphan customer_id
        ExtractedRecord(
            row_number=5,
            data={"order_id": "O4", "customer_id": "CUST-999-DOES-NOT-EXIST", "order_date": "2026-02-01", "amount": "30.00", "status": "PENDING", "updated_at": "2026-02-01T00:00:00Z"},
            raw_payload="raw4",
        ),
    ]

    valid, _ = validator.validate(records, run_id="r1", source_file="f.csv", quarantine_mgr=q_mgr, parent_keys=parent_keys)

    assert len(valid) == 1
    assert valid[0].data["order_id"] == "O1"
    assert q_mgr.count == 3
    rejection_codes = [r.rejection_code for r in q_mgr.records]
    assert "NEGATIVE_NUMERIC_VALUE" in rejection_codes
    assert "INVALID_STATUS_VALUE" in rejection_codes
    assert "ORPHAN_FOREIGN_KEY" in rejection_codes
