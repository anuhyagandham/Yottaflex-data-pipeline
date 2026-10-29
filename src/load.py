"""Atomic, idempotent SQLite persistence layer with retry handling and staging merge."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TypeVar

from src.config import (
    CUSTOMERS_DDL,
    ORDERS_DDL,
    PIPELINE_RUNS_DDL,
    QUARANTINE_RECORDS_DDL,
    DatasetConfig,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


def with_retry(
    max_retries: int = 3,
    base_delay: float = 0.1,
    backoff_factor: float = 2.0,
    retryable_exceptions: Tuple[type[Exception], ...] = (sqlite3.OperationalError,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator to retry operations on transient SQLite lock/busy errors with exponential backoff."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            attempts = 0
            delay = base_delay
            while True:
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    attempts += 1
                    if attempts > max_retries:
                        logger.error(
                            "Operation %s failed after %d retries: %s",
                            func.__name__,
                            max_retries,
                            e,
                        )
                        raise
                    logger.warning(
                        "Transient SQLite error in %s (%s). Retrying in %.2fs (attempt %d/%d)...",
                        func.__name__,
                        e,
                        delay,
                        attempts,
                        max_retries,
                    )
                    time.sleep(delay)
                    delay *= backoff_factor

        return wrapper

    return decorator


@dataclass
class LoadResult:
    """Outcome metrics of the database load operation."""

    inserted_count: int
    updated_count: int
    skipped_count: int


class SQLiteLoader:
    """Manages SQLite database connections, schema migrations, and idempotent upserts."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        """Creates a SQLite connection configured for high performance, concurrency, and integrity."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            isolation_level=None,  # We manage transactions explicitly
        )
        # Enable write-ahead logging (WAL) for concurrent reads and writes
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        return conn

    def init_database(self) -> None:
        """Initializes tables, indexes, and constraints if they do not exist."""
        full_ddl = f"""
        {PIPELINE_RUNS_DDL}
        {QUARANTINE_RECORDS_DDL}
        {CUSTOMERS_DDL}
        {ORDERS_DDL}
        CREATE TABLE IF NOT EXISTS audit_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            record_id TEXT NOT NULL,
            action TEXT NOT NULL,
            old_version INTEGER,
            new_version INTEGER,
            change_timestamp TEXT NOT NULL
        );
        """
        conn = self.get_connection()
        try:
            conn.executescript(full_ddl)
        finally:
            conn.close()

    def get_existing_customer_ids(self) -> Set[str]:
        """Fetches all valid customer IDs from the database for referential integrity checks."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT customer_id FROM customers;")
            return {row[0] for row in cursor.fetchall()}

    @with_retry(max_retries=3, base_delay=0.1)
    def load_dataset(
        self,
        config: DatasetConfig,
        records: List[Dict[str, Any]],
        batch_size: int = 5000,
    ) -> LoadResult:
        """Loads records using a temporary staging table with atomic upsert and version conflict resolution.

        Idempotency & Version Resolution Policy:
        - If record does not exist in target: INSERT (inserted_count += 1)
        - If record exists and incoming version/timestamp > existing version/timestamp: UPDATE (updated_count += 1)
        - If record exists and incoming version/timestamp <= existing: SKIP (skipped_count += 1)
        """
        if not records:
            return LoadResult(inserted_count=0, updated_count=0, skipped_count=0)

        table = config.table_name
        pk = config.primary_key
        ver_col = config.version_col

        conn = self.get_connection()
        conn.execute("BEGIN TRANSACTION;")

        try:
            # 1. Create a temporary staging table mirroring target table structure
            staging_table = f"_staging_{table}"
            conn.execute(f"DROP TABLE IF EXISTS {staging_table};")
            conn.execute(
                f"CREATE TEMP TABLE {staging_table} AS SELECT * FROM {table} WHERE 0;"
            )

            # Determine column names from the first transformed record
            sample_rec = records[0]
            cols = list(sample_rec.keys())
            placeholders = ", ".join(["?"] * len(cols))
            col_list_str = ", ".join(cols)

            insert_staging_sql = f"INSERT INTO {staging_table} ({col_list_str}) VALUES ({placeholders});"

            # 2. Bulk insert transformed records into staging table
            rows_to_insert = [
                tuple(rec.get(c) for c in cols)
                for rec in records
            ]
            conn.executemany(insert_staging_sql, rows_to_insert)

            # 3. Calculate Inserted, Updated, and Skipped counts via disjoint SQL partitions
            # Disjoint set 1: New records to insert (does not exist in target)
            count_new_query = f"""
            SELECT COUNT(*) FROM {staging_table} s
            WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{pk} = s.{pk});
            """
            inserted_count = conn.execute(count_new_query).fetchone()[0]

            # Disjoint set 2: Records existing in target with strictly newer version
            count_update_query = f"""
            SELECT COUNT(*) FROM {staging_table} s
            JOIN {table} t ON s.{pk} = t.{pk}
            WHERE s.{ver_col} > t.{ver_col};
            """
            updated_count = conn.execute(count_update_query).fetchone()[0]

            # Disjoint set 3: Records existing in target with older or equal version (skipped)
            count_skip_query = f"""
            SELECT COUNT(*) FROM {staging_table} s
            JOIN {table} t ON s.{pk} = t.{pk}
            WHERE s.{ver_col} <= t.{ver_col};
            """
            skipped_count = conn.execute(count_skip_query).fetchone()[0]

            # 4. Execute the DML mutations
            if inserted_count > 0:
                insert_query = f"""
                INSERT INTO {table} ({col_list_str})
                SELECT {col_list_str}
                FROM {staging_table} s
                WHERE NOT EXISTS (
                    SELECT 1 FROM {table} t WHERE t.{pk} = s.{pk}
                );
                """
                conn.execute(insert_query)

            if updated_count > 0:
                update_assignments = ", ".join(
                    [f"{c} = s.{c}" for c in cols if c not in (pk, "_version")]
                )
                update_query = f"""
                UPDATE {table}
                SET {update_assignments}, _version = {table}._version + 1
                FROM {staging_table} s
                WHERE {table}.{pk} = s.{pk}
                  AND s.{ver_col} > {table}.{ver_col};
                """
                conn.execute(update_query)

            # 5. Clean up staging table
            conn.execute(f"DROP TABLE IF EXISTS {staging_table};")

            # Commit the entire batch atomically
            conn.execute("COMMIT;")

            logger.info(
                "Loaded %s: %d inserted, %d updated, %d skipped",
                table,
                inserted_count,
                updated_count,
                skipped_count,
            )

            return LoadResult(
                inserted_count=inserted_count,
                updated_count=updated_count,
                skipped_count=skipped_count,
            )

        except Exception as e:
            conn.execute("ROLLBACK;")
            logger.error("Failed to load records into %s: %s", table, e)
            raise
        finally:
            conn.close()
