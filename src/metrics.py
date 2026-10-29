"""Run metrics tracking, throughput calculation, and SQLite execution persistence."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class PipelineMetrics:
    """Execution statistics for a single pipeline run."""

    run_id: str
    dataset_name: str
    source_file: str
    start_time: str = ""
    end_time: Optional[str] = None
    status: str = "PENDING"  # SUCCESS, FAILED, PARTIAL_SUCCESS
    input_count: int = 0
    valid_count: int = 0
    rejected_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    elapsed_seconds: float = 0.0
    throughput_records_per_sec: float = 0.0
    error_message: Optional[str] = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.start_time:
            now_iso = datetime.now(timezone.utc).isoformat()
            self.start_time = now_iso
            self.created_at = now_iso
        self._start_perf = time.perf_counter()

    def finish(self, status: str = "SUCCESS", error_message: Optional[str] = None) -> None:
        """Marks the run as completed and computes duration and throughput."""
        self.end_time = datetime.now(timezone.utc).isoformat()
        self.status = status
        self.error_message = error_message
        self.elapsed_seconds = round(time.perf_counter() - self._start_perf, 4)

        if self.elapsed_seconds > 0:
            self.throughput_records_per_sec = round(
                self.input_count / self.elapsed_seconds, 2
            )
        else:
            self.throughput_records_per_sec = float(self.input_count)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("_start_perf", None)
        return d

    def persist_to_db(self, conn: sqlite3.Connection) -> None:
        """Saves run metrics into the pipeline_runs database table."""
        upsert_sql = """
        INSERT INTO pipeline_runs (
            run_id, dataset_name, source_file, status, start_time, end_time,
            input_count, valid_count, rejected_count, inserted_count, updated_count, skipped_count,
            elapsed_seconds, throughput_records_per_sec, error_message, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            status = excluded.status,
            end_time = excluded.end_time,
            input_count = excluded.input_count,
            valid_count = excluded.valid_count,
            rejected_count = excluded.rejected_count,
            inserted_count = excluded.inserted_count,
            updated_count = excluded.updated_count,
            skipped_count = excluded.skipped_count,
            elapsed_seconds = excluded.elapsed_seconds,
            throughput_records_per_sec = excluded.throughput_records_per_sec,
            error_message = excluded.error_message;
        """
        conn.execute(
            upsert_sql,
            (
                self.run_id,
                self.dataset_name,
                self.source_file,
                self.status,
                self.start_time,
                self.end_time,
                self.input_count,
                self.valid_count,
                self.rejected_count,
                self.inserted_count,
                self.updated_count,
                self.skipped_count,
                self.elapsed_seconds,
                self.throughput_records_per_sec,
                self.error_message,
                self.created_at,
            ),
        )

    def to_summary_table(self) -> str:
        """Returns a formatted ASCII summary table of the run."""
        lines = [
            "=" * 60,
            f" PIPELINE RUN METRICS SUMMARY: {self.run_id}",
            "=" * 60,
            f" Dataset       : {self.dataset_name}",
            f" Source File   : {self.source_file}",
            f" Status        : {self.status}",
            f" Start Time    : {self.start_time}",
            f" End Time      : {self.end_time}",
            f" Elapsed (sec) : {self.elapsed_seconds:.4f} s",
            f" Throughput    : {self.throughput_records_per_sec:,.2f} records/sec",
            "-" * 60,
            f" Input Rows    : {self.input_count:,}",
            f" Valid Rows    : {self.valid_count:,}",
            f" Rejected Rows : {self.rejected_count:,}",
            "-" * 60,
            f" DB Inserted   : {self.inserted_count:,}",
            f" DB Updated    : {self.updated_count:,}",
            f" DB Skipped    : {self.skipped_count:,}",
            "=" * 60,
        ]
        if self.error_message:
            lines.append(f" Error         : {self.error_message}")
            lines.append("=" * 60)
        return "\n".join(lines)
