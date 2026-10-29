"""Command Line Interface (CLI) for the YottaFlex Data Engineering Pipeline."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from src.config import DATASET_CONFIGS
from src.generator import SyntheticDataGenerator
from src.load import SQLiteLoader
from src.logging_config import setup_logger
from src.pipeline import Pipeline, run_pipeline_suite
from src.reconcile import ReconciliationEngine

logger = logging.getLogger("cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="YottaFlex Reusable Data Engineering Pipeline Framework",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Command to run")

    # 1. generate-sample
    sub_sample = subparsers.add_parser(
        "generate-sample", help="Generate small synthetic sample datasets with edge cases"
    )
    sub_sample.add_argument(
        "--output-dir",
        default="data/sample",
        help="Target directory for sample CSVs (default: data/sample)",
    )

    # 2. generate-benchmark
    sub_gen_bench = subparsers.add_parser(
        "generate-benchmark", help="Generate large synthetic datasets for performance benchmarking"
    )
    sub_gen_bench.add_argument(
        "--output-dir",
        default="data/benchmark",
        help="Target directory for benchmark CSVs (default: data/benchmark)",
    )
    sub_gen_bench.add_argument(
        "--records",
        type=int,
        default=100000,
        help="Number of records per dataset (default: 100000)",
    )

    # 3. run
    sub_run = subparsers.add_parser("run", help="Run the ETL pipeline for a dataset or all")
    sub_run.add_argument(
        "--dataset",
        choices=["customers", "orders", "all"],
        default="all",
        help="Dataset pipeline to execute (default: all)",
    )
    sub_run.add_argument(
        "--db",
        default="output/pipeline.db",
        help="Path to target SQLite database (default: output/pipeline.db)",
    )
    sub_run.add_argument(
        "--input",
        default=None,
        help="Optional path to source CSV file. If omitted, uses data/sample files.",
    )

    # 4. reconcile
    sub_rec = subparsers.add_parser("reconcile", help="Run database reconciliation and constraint audit")
    sub_rec.add_argument(
        "--db",
        default="output/pipeline.db",
        help="Path to SQLite database to audit (default: output/pipeline.db)",
    )
    sub_rec.add_argument(
        "--report-file",
        default="reports/reconciliation_report.md",
        help="Path to write the markdown report (default: reports/reconciliation_report.md)",
    )

    # 5. benchmark
    sub_bench = subparsers.add_parser("benchmark", help="Execute high-scale performance benchmark")
    sub_bench.add_argument(
        "--records",
        type=int,
        default=100000,
        help="Number of records to benchmark (default: 100000)",
    )
    sub_bench.add_argument(
        "--db",
        default="output/benchmark.db",
        help="Path to SQLite database for benchmark (default: output/benchmark.db)",
    )

    # 6. report
    sub_rep = subparsers.add_parser("report", help="Display summary reports and quarantine audit")
    sub_rep.add_argument(
        "--db",
        default="output/pipeline.db",
        help="Path to SQLite database (default: output/pipeline.db)",
    )

    return parser


def handle_generate_sample(args: argparse.Namespace) -> int:
    generator = SyntheticDataGenerator(seed=42)
    c_path, o_path = generator.generate_sample_datasets(args.output_dir)
    print(f"\n[OK] Sample datasets generated successfully:")
    print(f"  - Customers: {c_path.resolve()}")
    print(f"  - Orders   : {o_path.resolve()}\n")
    return 0


def handle_generate_benchmark(args: argparse.Namespace) -> int:
    print(f"\n[*] Generating {args.records:,} records each for customers and orders...")
    generator = SyntheticDataGenerator(seed=42)
    c_path, o_path = generator.generate_benchmark_datasets(
        args.output_dir, customer_count=args.records, order_count=args.records
    )
    print(f"[OK] Benchmark datasets generated successfully:")
    print(f"  - Customers: {c_path.resolve()}")
    print(f"  - Orders   : {o_path.resolve()}\n")
    return 0


def handle_run(args: argparse.Namespace) -> int:
    db_path = Path(args.db)

    if args.dataset == "all":
        c_file = args.input or "data/sample/customers_sample.csv"
        o_file = "data/sample/orders_sample.csv"
        if not Path(c_file).exists() or not Path(o_file).exists():
            print("[*] Sample files not found. Auto-generating sample datasets first...")
            SyntheticDataGenerator(seed=42).generate_sample_datasets("data/sample")

        results = run_pipeline_suite(db_path, c_file, o_file)
        c_met = results["customers_metrics"]
        o_met = results["orders_metrics"]
        db_audit = results["database_audit"]

        print("\n" + "=" * 65)
        print("          PIPELINE SUITE EXECUTION COMPLETED")
        print("=" * 65)
        print(f" Database: {db_path.resolve()}\n")
        print(c_met.to_summary_table())
        print()
        print(o_met.to_summary_table())
        print("\n" + "=" * 65)
        print(f" GLOBAL DATABASE AUDIT STATUS: {'PASSED' if db_audit.all_passed else 'FAILED'}")
        print("=" * 65)
        for a in db_audit.assertions:
            icon = "[PASS]" if a.passed else "[FAIL]"
            print(f"  {icon} {a.name:<32}: {a.description}")
        print("=" * 65 + "\n")
        return 0 if (c_met.status != "FAILED" and o_met.status != "FAILED" and db_audit.all_passed) else 1

    else:
        dataset_name = args.dataset
        config = DATASET_CONFIGS[dataset_name]
        default_file = f"data/sample/{dataset_name}_sample.csv"
        source_file = args.input or default_file

        if not Path(source_file).exists():
            print(f"[*] Input file {source_file} not found. Generating sample data...")
            SyntheticDataGenerator(seed=42).generate_sample_datasets("data/sample")

        pipeline = Pipeline(config, db_path)
        metrics, reconcile_res = pipeline.run(source_file)

        print("\n" + metrics.to_summary_table())
        print("\nReconciliation Invariants:")
        for a in reconcile_res.assertions:
            icon = "[PASS]" if a.passed else "[FAIL]"
            print(f"  {icon} {a.name}: {a.details}")
        print()
        return 0 if metrics.status != "FAILED" and reconcile_res.all_passed else 1


def handle_reconcile(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERROR] Database file not found: {db_path}")
        return 1

    loader = SQLiteLoader(db_path)
    engine = ReconciliationEngine()

    with loader.get_connection() as conn:
        db_res = engine.reconcile_database(conn)

    report_md = engine.generate_markdown_report([], db_res)
    rep_path = Path(args.report_file)
    rep_path.parent.mkdir(parents=True, exist_ok=True)
    rep_path.write_text(report_md, encoding="utf-8")

    print("\n" + "=" * 65)
    print(f" DATABASE RECONCILIATION AUDIT: {db_path.name}")
    print("=" * 65)
    for a in db_res.assertions:
        icon = "[PASS]" if a.passed else "[FAIL]"
        print(f"  {icon} {a.name:<32}: {a.description}")
    print("=" * 65)
    print(f" Written markdown report to: {rep_path.resolve()}\n")
    return 0 if db_res.all_passed else 1


def handle_benchmark(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if db_path.exists():
        try:
            db_path.unlink()
        except Exception:
            pass

    records_count = args.records
    print(f"\n{'=' * 65}")
    print(f" EXECUTING HIGH-SCALE BENCHMARK ({records_count:,} records)")
    print(f"{'=' * 65}\n")

    bench_dir = Path("data/benchmark")
    generator = SyntheticDataGenerator(seed=42)
    print(f"[*] Generating benchmark datasets in {bench_dir}...")
    gen_start = time.perf_counter()
    c_csv, o_csv = generator.generate_benchmark_datasets(
        bench_dir, customer_count=records_count, order_count=records_count
    )
    gen_elapsed = time.perf_counter() - gen_start
    print(f"[OK] Generated {records_count:,} records in {gen_elapsed:.2f} seconds.\n")

    print(f"[*] Running benchmark pipeline on {db_path}...")
    suite_start = time.perf_counter()
    results = run_pipeline_suite(db_path, c_csv, o_csv)
    total_pipeline_time = time.perf_counter() - suite_start

    c_met = results["customers_metrics"]
    o_met = results["orders_metrics"]
    db_audit = results["database_audit"]

    total_records = c_met.input_count + o_met.input_count
    overall_throughput = total_records / total_pipeline_time if total_pipeline_time > 0 else 0

    print("\n" + "=" * 65)
    print("          BENCHMARK PERFORMANCE RESULTS")
    print("=" * 65)
    print(f" Total Records Processed : {total_records:,}")
    print(f" Total Pipeline Duration : {total_pipeline_time:.2f} seconds")
    print(f" Overall Throughput      : {overall_throughput:,.2f} records/second")
    print("-" * 65)
    print(f" Customers Duration      : {c_met.elapsed_seconds:.2f} s ({c_met.throughput_records_per_sec:,.2f} rec/s)")
    print(f" Orders Duration         : {o_met.elapsed_seconds:.2f} s ({o_met.throughput_records_per_sec:,.2f} rec/s)")
    print("-" * 65)
    print(f" Database Reconciliation : {'PASSED' if db_audit.all_passed else 'FAILED'}")
    print("=" * 65 + "\n")
    return 0


def handle_report(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERROR] Database file {db_path} does not exist.")
        return 1

    loader = SQLiteLoader(db_path)
    with loader.get_connection() as conn:
        print("\n" + "=" * 70)
        print(" PIPELINE RUNS HISTORY")
        print("=" * 70)
        runs = conn.execute(
            """SELECT run_id, dataset_name, status, input_count, valid_count, 
                      rejected_count, inserted_count, updated_count, skipped_count, 
                      elapsed_seconds, throughput_records_per_sec 
               FROM pipeline_runs ORDER BY created_at DESC;"""
        ).fetchall()

        if not runs:
            print("No pipeline runs recorded.")
        else:
            header = f"{'Run ID':<22} {'Dataset':<10} {'Status':<14} {'Input':<8} {'Valid':<8} {'Rej':<6} {'Ins':<8} {'Time(s)':<8} {'Rec/s':<10}"
            print(header)
            print("-" * len(header))
            for r in runs:
                print(f"{r[0]:<22} {r[1]:<10} {r[2]:<14} {r[3]:<8} {r[4]:<8} {r[5]:<6} {r[6]:<8} {r[9]:<8.2f} {r[10]:<10.1f}")

        print("\n" + "=" * 70)
        print(" QUARANTINE BREAKDOWN BY REJECTION CODE")
        print("=" * 70)
        q_rows = conn.execute(
            """SELECT dataset_name, rejection_stage, rejection_code, COUNT(*) 
               FROM quarantine_records 
               GROUP BY dataset_name, rejection_stage, rejection_code 
               ORDER BY COUNT(*) DESC;"""
        ).fetchall()

        if not q_rows:
            print("Zero records in quarantine.")
        else:
            print(f"{'Dataset':<12} {'Stage':<22} {'Rejection Code':<28} {'Count':<6}")
            print("-" * 70)
            for qr in q_rows:
                print(f"{qr[0]:<12} {qr[1]:<22} {qr[2]:<28} {qr[3]:<6}")

        print("=" * 70 + "\n")
    return 0


def main() -> None:
    setup_logger(log_file="output/pipeline.log")
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "generate-sample": handle_generate_sample,
        "generate-benchmark": handle_generate_benchmark,
        "run": handle_run,
        "reconcile": handle_reconcile,
        "benchmark": handle_benchmark,
        "report": handle_report,
    }

    handler = dispatch.get(args.command)
    if not handler:
        parser.print_help()
        sys.exit(1)

    sys.exit(handler(args))


if __name__ == "__main__":
    main()
