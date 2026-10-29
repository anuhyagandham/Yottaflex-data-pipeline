"""Automated mathematical reconciliation and database integrity verification engine."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.config import ALLOWED_ORDER_STATUSES
from src.metrics import PipelineMetrics

logger = logging.getLogger(__name__)


@dataclass
class AssertionResult:
    """Individual assertion check in a reconciliation suite."""

    name: str
    description: str
    expected: Any
    actual: Any
    passed: bool
    details: str = ""


@dataclass
class RunReconciliationResult:
    """Reconciliation outcome for an individual pipeline execution."""

    run_id: str
    dataset_name: str
    all_passed: bool
    assertions: List[AssertionResult] = field(default_factory=list)


@dataclass
class DatabaseReconciliationResult:
    """Global database consistency and constraint audit results."""

    all_passed: bool
    assertions: List[AssertionResult] = field(default_factory=list)
    customers_count: int = 0
    orders_count: int = 0
    quarantine_count: int = 0
    runs_count: int = 0


class ReconciliationEngine:
    """Verifies mathematical conservation laws, primary-key uniqueness, and referential integrity."""

    def reconcile_run(
        self,
        metrics: PipelineMetrics,
        conn: sqlite3.Connection,
        in_batch_duplicates: int = 0,
    ) -> RunReconciliationResult:
        """Evaluates pipeline invariants for an individual run."""
        assertions: List[AssertionResult] = []

        # Invariant 1: Total Input = Valid + Rejected (Quarantined)
        expected_input = metrics.valid_count + metrics.rejected_count
        passed_1 = metrics.input_count == expected_input
        assertions.append(
            AssertionResult(
                name="RECORD_CONSERVATION_LAW",
                description="Total input rows must strictly equal valid rows plus rejected rows",
                expected=metrics.input_count,
                actual=expected_input,
                passed=passed_1,
                details=f"Input ({metrics.input_count}) == Valid ({metrics.valid_count}) + Rejected ({metrics.rejected_count})",
            )
        )

        # Invariant 2: Ingestion Conservation Law
        # Valid rows must equal inserted + updated + skipped + in-batch duplicates resolved
        db_disposition = (
            metrics.inserted_count
            + metrics.updated_count
            + metrics.skipped_count
            + in_batch_duplicates
        )
        passed_2 = metrics.valid_count == db_disposition
        assertions.append(
            AssertionResult(
                name="INGESTION_DISPOSITION_LAW",
                description="Valid rows must strictly equal inserted + updated + skipped + in-batch duplicates",
                expected=metrics.valid_count,
                actual=db_disposition,
                passed=passed_2,
                details=(
                    f"Valid ({metrics.valid_count}) == Inserted ({metrics.inserted_count}) + "
                    f"Updated ({metrics.updated_count}) + Skipped ({metrics.skipped_count}) + "
                    f"In-Batch Dups ({in_batch_duplicates})"
                ),
            )
        )

        # Invariant 3: Quarantine DB Record Count matches rejected count
        q_count = conn.execute(
            "SELECT COUNT(*) FROM quarantine_records WHERE run_id = ?;",
            (metrics.run_id,),
        ).fetchone()[0]
        passed_3 = q_count == metrics.rejected_count
        assertions.append(
            AssertionResult(
                name="QUARANTINE_PERSISTENCE_AUDIT",
                description="Number of records persisted in quarantine_records table must match rejected_count",
                expected=metrics.rejected_count,
                actual=q_count,
                passed=passed_3,
                details=f"Metrics rejected={metrics.rejected_count}, DB quarantine={q_count}",
            )
        )

        all_passed = all(a.passed for a in assertions)
        return RunReconciliationResult(
            run_id=metrics.run_id,
            dataset_name=metrics.dataset_name,
            all_passed=all_passed,
            assertions=assertions,
        )

    def reconcile_database(self, conn: sqlite3.Connection) -> DatabaseReconciliationResult:
        """Audits database-level invariants: PK uniqueness, FK referential integrity, domain constraints."""
        assertions: List[AssertionResult] = []

        # 1. Total counts
        cust_count = conn.execute("SELECT COUNT(*) FROM customers;").fetchone()[0]
        orders_count = conn.execute("SELECT COUNT(*) FROM orders;").fetchone()[0]
        q_count = conn.execute("SELECT COUNT(*) FROM quarantine_records;").fetchone()[0]
        runs_count = conn.execute("SELECT COUNT(*) FROM pipeline_runs;").fetchone()[0]

        # 2. Customers Primary Key Uniqueness
        cust_distinct = conn.execute(
            "SELECT COUNT(DISTINCT customer_id) FROM customers;"
        ).fetchone()[0]
        passed_cust_pk = cust_count == cust_distinct
        assertions.append(
            AssertionResult(
                name="CUSTOMERS_PK_UNIQUENESS",
                description="Every customer record must have a unique customer_id",
                expected=cust_count,
                actual=cust_distinct,
                passed=passed_cust_pk,
                details=f"Total: {cust_count}, Distinct PKs: {cust_distinct}",
            )
        )

        # 3. Orders Primary Key Uniqueness
        orders_distinct = conn.execute(
            "SELECT COUNT(DISTINCT order_id) FROM orders;"
        ).fetchone()[0]
        passed_orders_pk = orders_count == orders_distinct
        assertions.append(
            AssertionResult(
                name="ORDERS_PK_UNIQUENESS",
                description="Every order record must have a unique order_id",
                expected=orders_count,
                actual=orders_distinct,
                passed=passed_orders_pk,
                details=f"Total: {orders_count}, Distinct PKs: {orders_distinct}",
            )
        )

        # 4. Referential Integrity (No orphan orders)
        orphan_query = """
        SELECT COUNT(*)
        FROM orders o
        LEFT JOIN customers c ON o.customer_id = c.customer_id
        WHERE c.customer_id IS NULL;
        """
        orphan_count = conn.execute(orphan_query).fetchone()[0]
        passed_fk = orphan_count == 0
        assertions.append(
            AssertionResult(
                name="ORDERS_FK_REFERENTIAL_INTEGRITY",
                description="Zero orders may exist referencing non-existent customer_ids",
                expected=0,
                actual=orphan_count,
                passed=passed_fk,
                details=f"Orphan order count: {orphan_count}",
            )
        )

        # 5. Non-negative Amount Constraint
        neg_count = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE amount < 0;"
        ).fetchone()[0]
        passed_neg = neg_count == 0
        assertions.append(
            AssertionResult(
                name="ORDERS_AMOUNT_NON_NEGATIVE",
                description="Zero orders may have negative amount",
                expected=0,
                actual=neg_count,
                passed=passed_neg,
                details=f"Negative amount count: {neg_count}",
            )
        )

        # 6. Status Allowed Set Constraint
        status_placeholders = ", ".join(["?"] * len(ALLOWED_ORDER_STATUSES))
        invalid_status_query = f"""
        SELECT COUNT(*) FROM orders WHERE status NOT IN ({status_placeholders});
        """
        invalid_status_count = conn.execute(
            invalid_status_query, tuple(ALLOWED_ORDER_STATUSES)
        ).fetchone()[0]
        passed_status = invalid_status_count == 0
        assertions.append(
            AssertionResult(
                name="ORDERS_STATUS_DOMAIN_INTEGRITY",
                description="All order statuses must belong to the documented allowed set",
                expected=0,
                actual=invalid_status_count,
                passed=passed_status,
                details=f"Invalid status count: {invalid_status_count}",
            )
        )

        # 7. Derived Field Accuracy (amount_cents == round(amount * 100))
        drift_cents = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE amount_cents != CAST(ROUND(amount * 100) AS INTEGER);"
        ).fetchone()[0]
        passed_cents = drift_cents == 0
        assertions.append(
            AssertionResult(
                name="ORDERS_AMOUNT_CENTS_DERIVATION",
                description="Derived amount_cents must exactly match integer conversion of amount * 100",
                expected=0,
                actual=drift_cents,
                passed=passed_cents,
                details=f"Mismatched cents count: {drift_cents}",
            )
        )

        all_passed = all(a.passed for a in assertions)
        return DatabaseReconciliationResult(
            all_passed=all_passed,
            assertions=assertions,
            customers_count=cust_count,
            orders_count=orders_count,
            quarantine_count=q_count,
            runs_count=runs_count,
        )

    def generate_markdown_report(
        self,
        run_results: List[RunReconciliationResult],
        db_result: DatabaseReconciliationResult,
    ) -> str:
        """Generates a professional Markdown audit report suitable for technical review."""
        md = ["# Pipeline & Database Reconciliation Audit Report", ""]
        md.append("## 1. Executive Summary")
        overall_status = "PASSED" if all(r.all_passed for r in run_results) and db_result.all_passed else "FAILED"
        md.append(f"- **Overall Audit Status**: **{overall_status}**")
        md.append(f"- **Total Customers Ingested**: `{db_result.customers_count:,}`")
        md.append(f"- **Total Orders Ingested**: `{db_result.orders_count:,}`")
        md.append(f"- **Total Records Quarantined**: `{db_result.quarantine_count:,}`")
        md.append(f"- **Pipeline Runs Logged**: `{db_result.runs_count:,}`")
        md.append("")

        md.append("## 2. Per-Run Conservation Law Invariants")
        for rr in run_results:
            md.append(f"### Run: `{rr.run_id}` ({rr.dataset_name}) - Status: {'PASS' if rr.all_passed else 'FAIL'}")
            md.append("| Assertion | Expected | Actual | Status | Details |")
            md.append("|:---|:---:|:---:|:---:|:---|")
            for a in rr.assertions:
                status_icon = "PASS" if a.passed else "FAIL"
                md.append(f"| `{a.name}` | {a.expected} | {a.actual} | {status_icon} | {a.details} |")
            md.append("")

        md.append("## 3. Database State & Referential Integrity Invariants")
        md.append("| Check | Expected | Actual | Status | Description |")
        md.append("|:---|:---:|:---:|:---:|:---|")
        for a in db_result.assertions:
            status_icon = "PASS" if a.passed else "FAIL"
            md.append(f"| `{a.name}` | {a.expected} | {a.actual} | {status_icon} | {a.description} |")
        md.append("")

        return "\n".join(md)
