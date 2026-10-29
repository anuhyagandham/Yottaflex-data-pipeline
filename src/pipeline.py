"""End-to-end pipeline orchestrator coordinating extract, validate, transform, load, and reconcile."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.config import DATASET_CONFIGS, DatasetConfig
from src.extract import CSVExtractor
from src.load import SQLiteLoader
from src.logging_config import current_run_id
from src.metrics import PipelineMetrics
from src.quarantine import QuarantineManager
from src.reconcile import ReconciliationEngine, RunReconciliationResult
from src.transform import DataTransformer
from src.validate import BusinessValidator, SchemaValidator

logger = logging.getLogger(__name__)


class Pipeline:
    """Orchestrates the complete resilient ETL process for a single dataset."""

    def __init__(self, config: DatasetConfig, db_path: Path | str):
        self.config = config
        self.db_path = Path(db_path)
        self.loader = SQLiteLoader(self.db_path)
        self.reconciler = ReconciliationEngine()

    def run(
        self,
        file_path: Path | str,
        custom_run_id: Optional[str] = None,
    ) -> Tuple[PipelineMetrics, RunReconciliationResult]:
        """Executes the pipeline on the specified source CSV file."""
        run_id = custom_run_id or f"run_{self.config.name}_{uuid.uuid4().hex[:8]}"
        current_run_id.set(run_id)

        source_path = Path(file_path)
        metrics = PipelineMetrics(
            run_id=run_id,
            dataset_name=self.config.name,
            source_file=source_path.name,
        )
        quarantine_mgr = QuarantineManager()

        logger.info(
            "Starting pipeline run %s for dataset '%s' from %s",
            run_id,
            self.config.name,
            source_path.name,
        )

        # Ensure database tables exist
        self.loader.init_database()

        in_batch_duplicates_resolved = 0

        try:
            # 1. EXTRACT
            extractor = CSVExtractor(self.config)
            extract_result = extractor.extract(
                source_path, run_id=run_id, quarantine_mgr=quarantine_mgr
            )

            # Record total inputs
            # Total input rows = successfully extracted rows + extraction rejections (malformed rows)
            metrics.input_count = len(extract_result.records) + quarantine_mgr.count

            # If file was completely empty or had no valid rows
            if not extract_result.records and quarantine_mgr.count == 0:
                logger.info("File %s is empty. Pipeline finished cleanly with 0 rows.", source_path.name)
                metrics.finish(status="SUCCESS")
                with self.loader.get_connection() as conn:
                    metrics.persist_to_db(conn)
                reconcile_result = self.reconciler.reconcile_run(metrics, conn, 0)
                return metrics, reconcile_result

            # 2. SCHEMA VALIDATION
            schema_validator = SchemaValidator(self.config)
            schema_valid_records, _ = schema_validator.validate(
                records=extract_result.records,
                run_id=run_id,
                source_file=source_path.name,
                quarantine_mgr=quarantine_mgr,
            )

            # 3. DOMAIN BUSINESS VALIDATION & REFERENTIAL INTEGRITY
            parent_keys: Optional[set[str]] = None
            if self.config.name == "orders":
                parent_keys = self.loader.get_existing_customer_ids()

            business_validator = BusinessValidator(self.config)
            business_valid_records, _ = business_validator.validate(
                records=schema_valid_records,
                run_id=run_id,
                source_file=source_path.name,
                quarantine_mgr=quarantine_mgr,
                parent_keys=parent_keys,
            )

            # Valid records count before in-batch deduplication
            metrics.valid_count = len(business_valid_records)
            metrics.rejected_count = quarantine_mgr.count

            # 4. TRANSFORM & IN-BATCH DEDUPLICATION
            transformer = DataTransformer(self.config)
            transformed_records, dups_resolved = transformer.transform_all(
                business_valid_records, source_file=source_path.name
            )
            in_batch_duplicates_resolved = dups_resolved

            # 5. ATOMIC DATABASE LOAD
            load_result = self.loader.load_dataset(self.config, transformed_records)
            metrics.inserted_count = load_result.inserted_count
            metrics.updated_count = load_result.updated_count
            metrics.skipped_count = load_result.skipped_count

            # 6. PERSIST QUARANTINE RECORDS
            with self.loader.get_connection() as conn:
                conn.execute("BEGIN TRANSACTION;")
                try:
                    quarantine_mgr.persist_to_db(conn)
                    conn.execute("COMMIT;")
                except Exception:
                    conn.execute("ROLLBACK;")
                    raise

            # 7. RECONCILIATION & AUDIT
            metrics.finish(
                status="PARTIAL_SUCCESS" if metrics.rejected_count > 0 else "SUCCESS"
            )

            with self.loader.get_connection() as conn:
                # Persist metrics
                metrics.persist_to_db(conn)
                reconcile_result = self.reconciler.reconcile_run(
                    metrics, conn, in_batch_duplicates=in_batch_duplicates_resolved
                )

            logger.info("\n%s", metrics.to_summary_table())
            return metrics, reconcile_result

        except Exception as e:
            logger.exception("Fatal unhandled exception in pipeline run %s: %s", run_id, e)
            metrics.finish(status="FAILED", error_message=str(e))
            try:
                with self.loader.get_connection() as conn:
                    metrics.persist_to_db(conn)
            except Exception as db_err:
                logger.error("Failed to persist run failure metrics: %s", db_err)

            # Generate partial reconciliation on failure
            with self.loader.get_connection() as conn:
                reconcile_result = self.reconciler.reconcile_run(metrics, conn, 0)
            raise


def run_pipeline_suite(
    db_path: Path | str,
    customers_file: Path | str,
    orders_file: Path | str,
) -> Dict[str, Any]:
    """Executes the full pipeline in strict dependency order (Customers -> Orders)."""
    db_p = Path(db_path)
    logger.info("Executing dependency-ordered pipeline suite on database: %s", db_p)

    # 1. Run Customers Pipeline first
    cust_pipeline = Pipeline(DATASET_CONFIGS["customers"], db_p)
    cust_metrics, cust_reconcile = cust_pipeline.run(customers_file)

    # 2. Run Orders Pipeline second (benefits from customers in DB for FK validation)
    orders_pipeline = Pipeline(DATASET_CONFIGS["orders"], db_p)
    orders_metrics, orders_reconcile = orders_pipeline.run(orders_file)

    # 3. Run Global Database Reconciliation Audit
    with cust_pipeline.loader.get_connection() as conn:
        db_audit = cust_pipeline.reconciler.reconcile_database(conn)

    return {
        "customers_metrics": cust_metrics,
        "customers_reconcile": cust_reconcile,
        "orders_metrics": orders_metrics,
        "orders_reconcile": orders_reconcile,
        "database_audit": db_audit,
    }
