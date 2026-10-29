"""Reusable CSV extraction module with row-level error tolerance and drift detection."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import DatasetConfig
from src.quarantine import QuarantineManager, QuarantineRecord

logger = logging.getLogger(__name__)


@dataclass
class ExtractedRecord:
    """Individual parsed record from the raw CSV input."""

    row_number: int
    data: Dict[str, str]
    raw_payload: str


@dataclass
class ExtractionResult:
    """Container for the output of the extraction stage."""

    records: List[ExtractedRecord]
    quarantined: List[QuarantineRecord]
    headers: List[str]
    total_raw_rows: int
    drift_detected: bool = False
    extra_columns: List[str] = field(default_factory=list)
    missing_columns: List[str] = field(default_factory=list)


class CSVExtractor:
    """Extracts CSV data with graceful handling of malformed lines, encoding, and drift."""

    def __init__(self, config: DatasetConfig):
        self.config = config

    def extract(
        self,
        file_path: Path | str,
        run_id: str,
        quarantine_mgr: Optional[QuarantineManager] = None,
    ) -> ExtractionResult:
        """Extracts records from a CSV file.

        Args:
            file_path: Path to the CSV file.
            run_id: Unique pipeline execution identifier.
            quarantine_mgr: Optional QuarantineManager to append rejected rows to.

        Returns:
            ExtractionResult containing successfully parsed rows and extraction rejections.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Source file not found: {path.resolve()}")

        # Empty file check (0 bytes)
        if path.stat().st_size == 0:
            logger.warning("Source file %s is completely empty (0 bytes).", path.name)
            return ExtractionResult(
                records=[],
                quarantined=[],
                headers=[],
                total_raw_rows=0,
                drift_detected=False,
            )

        q_mgr = quarantine_mgr or QuarantineManager()
        records: List[ExtractedRecord] = []
        headers: List[str] = []
        extra_columns: List[str] = []
        missing_columns: List[str] = []
        total_rows = 0

        # Support UTF-8 and UTF-8 with BOM (utf-8-sig)
        with open(path, mode="r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)

            try:
                first_row = next(reader, None)
            except Exception as e:
                logger.error("Failed to read header of %s: %s", path.name, e)
                q_mgr.add_rejection(
                    run_id=run_id,
                    dataset_name=self.config.name,
                    source_file=path.name,
                    source_row_number=1,
                    raw_payload="<UNREADABLE_FILE_HEADER>",
                    rejection_stage="EXTRACT",
                    rejection_code="CORRUPT_HEADER",
                    rejection_reason=f"Failed to read file header: {e}",
                )
                return ExtractionResult(
                    records=[],
                    quarantined=q_mgr.records,
                    headers=[],
                    total_raw_rows=0,
                )

            if first_row is None:
                # File had whitespace or was completely empty
                return ExtractionResult(
                    records=[],
                    quarantined=[],
                    headers=[],
                    total_raw_rows=0,
                )

            # Clean and strip header column names
            headers = [h.strip() for h in first_row]

            # Detect missing required columns
            expected_required = set(self.config.required_columns)
            present_cols = set(headers)
            missing = list(expected_required - present_cols)
            if missing:
                missing_columns = missing
                logger.warning(
                    "Missing required columns in %s: %s", path.name, missing
                )

            # Detect schema drift (extra columns not defined in config)
            all_known_cols = set(self.config.all_column_names)
            extras = list(present_cols - all_known_cols)
            if extras:
                extra_columns = extras
                logger.info(
                    "Schema drift detected in %s. Extra columns: %s",
                    path.name,
                    extras,
                )

            expected_len = len(headers)
            current_row_num = 1  # 1 was header

            for row in reader:
                current_row_num += 1
                total_rows += 1

                # Skip completely blank lines in CSV
                if not row or all(c.strip() == "" for c in row):
                    continue

                raw_line_repr = ",".join(row)

                # Check for malformed row where column count does not match header
                if len(row) != expected_len:
                    logger.warning(
                        "Malformed CSV row %d in %s: expected %d fields, got %d",
                        current_row_num,
                        path.name,
                        expected_len,
                        len(row),
                    )
                    q_mgr.add_rejection(
                        run_id=run_id,
                        dataset_name=self.config.name,
                        source_file=path.name,
                        source_row_number=current_row_num,
                        raw_payload=raw_line_repr,
                        rejection_stage="EXTRACT",
                        rejection_code="MALFORMED_CSV_ROW",
                        rejection_reason=f"Expected {expected_len} columns but received {len(row)}",
                    )
                    continue

                # Build record dictionary
                record_data = {headers[i]: row[i].strip() for i in range(expected_len)}
                records.append(
                    ExtractedRecord(
                        row_number=current_row_num,
                        data=record_data,
                        raw_payload=raw_line_repr,
                    )
                )

        drift = len(extra_columns) > 0
        return ExtractionResult(
            records=records,
            quarantined=q_mgr.records,
            headers=headers,
            total_raw_rows=total_rows,
            drift_detected=drift,
            extra_columns=extra_columns,
            missing_columns=missing_columns,
        )
