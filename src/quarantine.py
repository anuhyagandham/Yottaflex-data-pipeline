"""Quarantine and reject traceability system for invalid or non-compliant records."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class QuarantineRecord:
    """Represents an individual rejected record preserved for audit and remediation."""

    run_id: str
    dataset_name: str
    source_file: str
    source_row_number: Optional[int]
    raw_payload: str  # JSON-encoded raw string or record
    rejection_stage: str  # EXTRACT, SCHEMA_VALIDATION, BUSINESS_VALIDATION, INTEGRITY_CHECK
    rejection_code: str  # e.g., MALFORMED_ROW, MISSING_REQUIRED_FIELD, INVALID_EMAIL
    rejection_reason: str
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            object.__setattr__(
                self, "created_at", datetime.now(timezone.utc).isoformat()
            )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class QuarantineManager:
    """Manages collection, statistics, and database persistence of quarantined records."""

    def __init__(self) -> None:
        self._records: List[QuarantineRecord] = []

    @property
    def records(self) -> List[QuarantineRecord]:
        return list(self._records)

    @property
    def count(self) -> int:
        return len(self._records)

    def add(self, record: QuarantineRecord) -> None:
        self._records.append(record)

    def add_rejection(
        self,
        run_id: str,
        dataset_name: str,
        source_file: str,
        source_row_number: Optional[int],
        raw_payload: Any,
        rejection_stage: str,
        rejection_code: str,
        rejection_reason: str,
    ) -> QuarantineRecord:
        """Helper to create and track a rejection in a single call."""
        if not isinstance(raw_payload, str):
            try:
                payload_str = json.dumps(raw_payload, ensure_ascii=False)
            except Exception:
                payload_str = str(raw_payload)
        else:
            payload_str = raw_payload

        record = QuarantineRecord(
            run_id=run_id,
            dataset_name=dataset_name,
            source_file=source_file,
            source_row_number=source_row_number,
            raw_payload=payload_str,
            rejection_stage=rejection_stage,
            rejection_code=rejection_code,
            rejection_reason=rejection_reason,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.add(record)
        return record

    def persist_to_db(self, conn: sqlite3.Connection) -> int:
        """Persists all collected quarantine records into the database atomically."""
        if not self._records:
            return 0

        insert_sql = """
        INSERT INTO quarantine_records (
            run_id, dataset_name, source_file, source_row_number,
            raw_payload, rejection_stage, rejection_code, rejection_reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        rows = [
            (
                r.run_id,
                r.dataset_name,
                r.source_file,
                r.source_row_number,
                r.raw_payload,
                r.rejection_stage,
                r.rejection_code,
                r.rejection_reason,
                r.created_at,
            )
            for r in self._records
        ]
        conn.executemany(insert_sql, rows)
        return len(rows)

    def get_summary(self) -> Dict[str, Any]:
        """Returns aggregate metrics on rejections by stage and error code."""
        by_stage: Dict[str, int] = {}
        by_code: Dict[str, int] = {}

        for r in self._records:
            by_stage[r.rejection_stage] = by_stage.get(r.rejection_stage, 0) + 1
            by_code[r.rejection_code] = by_code.get(r.rejection_code, 0) + 1

        return {
            "total_quarantined": len(self._records),
            "by_stage": by_stage,
            "by_code": by_code,
        }
