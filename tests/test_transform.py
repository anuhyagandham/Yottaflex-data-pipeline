"""Tests for transformation, enrichment, and deterministic in-batch deduplication."""

from __future__ import annotations

from src.config import CUSTOMERS_CONFIG, ORDERS_CONFIG
from src.extract import ExtractedRecord
from src.transform import DataTransformer


def test_transform_normalizations_and_derived_fields() -> None:
    transformer = DataTransformer(ORDERS_CONFIG)
    rec = ExtractedRecord(
        row_number=2,
        data={
            "order_id": "ORD-001 ",
            "customer_id": " CUST-001",
            "order_date": "2026-02-01",
            "amount": " 99.99 ",
            "status": "pending",
            "updated_at": "2026-02-01T15:30:00Z",
        },
        raw_payload="raw",
    )

    result = transformer.transform_record(rec, source_file="orders.csv", ingestion_timestamp="2026-02-01T16:00:00Z")

    assert result["order_id"] == "ORD-001"
    assert result["customer_id"] == "CUST-001"
    assert result["status"] == "PENDING"
    assert result["amount"] == 99.99
    assert result["amount_cents"] == 9999
    assert result["_source_file"] == "orders.csv"
    assert result["_version"] == 1


def test_in_batch_deduplication_by_version_timestamp() -> None:
    transformer = DataTransformer(ORDERS_CONFIG)

    # 3 records with the SAME order_id 'ORD-001', but different updated_at timestamps
    records = [
        ExtractedRecord(
            row_number=2,
            data={"order_id": "ORD-001", "customer_id": "C1", "order_date": "2026-02-01", "amount": "10.00", "status": "PENDING", "updated_at": "2026-02-01T10:00:00Z"},
            raw_payload="r1",
        ),
        ExtractedRecord(
            row_number=5,
            data={"order_id": "ORD-001", "customer_id": "C1", "order_date": "2026-02-01", "amount": "15.00", "status": "COMPLETED", "updated_at": "2026-02-01T12:00:00Z"},
            raw_payload="r2",
        ),
        ExtractedRecord(
            row_number=8,
            data={"order_id": "ORD-001", "customer_id": "C1", "order_date": "2026-02-01", "amount": "12.00", "status": "PROCESSING", "updated_at": "2026-02-01T11:00:00Z"},
            raw_payload="r3",
        ),
    ]

    deduped, dups_count = transformer.transform_all(records, source_file="orders.csv")

    assert len(deduped) == 1
    assert dups_count == 2
    # Winner must be row 5 with the latest timestamp '12:00:00Z' and amount 15.00
    assert deduped[0]["amount"] == 15.00
    assert deduped[0]["status"] == "COMPLETED"


def test_in_batch_deduplication_tie_breaker_by_row_number() -> None:
    transformer = DataTransformer(CUSTOMERS_CONFIG)

    # 2 records with IDENTICAL customer_id and IDENTICAL created_at
    records = [
        ExtractedRecord(
            row_number=3,
            data={"customer_id": "CUST-01", "customer_name": "First Version", "email": "first@ex.com", "city": "NYC", "created_at": "2026-01-01T00:00:00Z"},
            raw_payload="r1",
        ),
        ExtractedRecord(
            row_number=9,
            data={"customer_id": "CUST-01", "customer_name": "Second Version", "email": "second@ex.com", "city": "NYC", "created_at": "2026-01-01T00:00:00Z"},
            raw_payload="r2",
        ),
    ]

    deduped, dups_count = transformer.transform_all(records, source_file="customers.csv")

    assert len(deduped) == 1
    assert dups_count == 1
    # Deterministic tie-breaker: row 9 appears later in source file, so it wins
    assert deduped[0]["customer_name"] == "Second Version"
    assert deduped[0]["email"] == "second@ex.com"
