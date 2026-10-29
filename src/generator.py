"""Deterministic synthetic data generator for testing, edge cases, and benchmarks."""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from src.config import ALLOWED_ORDER_STATUSES


class SyntheticDataGenerator:
    """Generates reproducible customer and order CSV datasets with controlled edge cases."""

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = random.Random(seed)

    def generate_sample_datasets(
        self, output_dir: Path | str
    ) -> Tuple[Path, Path]:
        """Generates small, high-density sample datasets with deliberate edge cases for review."""
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        customers_path = out_path / "customers_sample.csv"
        orders_path = out_path / "orders_sample.csv"

        # ---------------------------------------------------------
        # 1. Generate Customers Sample (50 rows)
        # ---------------------------------------------------------
        cities = ["Seattle", "San Francisco", "Austin", "New York", "Chicago", "Boston", "Denver", ""]
        first_names = ["Alice", "Bob", "Charlie", "Diana", "Ethan", "Fiona", "George", "Hannah", "Ian", "Julia"]
        last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez"]

        base_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        cust_rows: List[List[str]] = []

        # We will generate 45 valid customers
        valid_cust_ids = [f"CUST-{i:04d}" for i in range(1, 46)]
        for i, cid in enumerate(valid_cust_ids):
            fname = self.rng.choice(first_names)
            lname = self.rng.choice(last_names)
            name = f"{fname} {lname}"
            email = f"{fname.lower()}.{lname.lower()}{i}@example.com"
            city = self.rng.choice(cities)
            created_at = (base_time + timedelta(days=i, hours=self.rng.randint(1, 12))).isoformat()
            cust_rows.append([cid, name, email, city, created_at])

        # Deliberate Edge Cases for Customers:
        # 1. In-batch duplicate customer_id with conflicting timestamps (test deterministic deduplication)
        dup_winner_time = (base_time + timedelta(days=50)).isoformat()
        cust_rows.append(["CUST-0001", "Alice Smith Updated", "alice.updated@example.com", "Austin", dup_winner_time])

        # 2. Missing required email (empty field)
        cust_rows.append(["CUST-0096", "Missing Email User", "", "Seattle", base_time.isoformat()])

        # 3. Invalid email format
        cust_rows.append(["CUST-0097", "Bad Email User", "not-an-email-at-all", "Boston", base_time.isoformat()])

        # 4. Invalid timestamp format
        cust_rows.append(["CUST-0098", "Bad Timestamp User", "bad.time@example.com", "Denver", "invalid-2026-99-99"])

        # 5. Malformed CSV line (simulated by writing directly with extra field)
        malformed_cust_row = ["CUST-0099", "Malformed Line User", "malformed@example.com", "Miami", base_time.isoformat(), "EXTRA_FIELD_UNEXPECTED"]

        # Write customers CSV
        with open(customers_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # Include a schema drift column in header: 'loyalty_tier'
            writer.writerow(["customer_id", "customer_name", "email", "city", "created_at", "loyalty_tier"])
            for r in cust_rows:
                writer.writerow(r + ["GOLD"])
            # Write malformed row with one extra column
            writer.writerow(malformed_cust_row + ["ANOTHER_EXTRA"])

        # ---------------------------------------------------------
        # 2. Generate Orders Sample (100 rows)
        # ---------------------------------------------------------
        statuses = list(ALLOWED_ORDER_STATUSES)
        order_rows: List[List[str]] = []

        for i in range(1, 95):
            oid = f"ORD-{i:05d}"
            cid = self.rng.choice(valid_cust_ids)
            order_date = (base_time + timedelta(days=self.rng.randint(1, 30))).strftime("%Y-%m-%d")
            amount = f"{self.rng.uniform(5.0, 999.0):.2f}"
            status = self.rng.choice(statuses)
            updated_at = (base_time + timedelta(days=self.rng.randint(31, 60))).isoformat()
            order_rows.append([oid, cid, order_date, amount, status, updated_at])

        # Deliberate Edge Cases for Orders:
        # 1. In-batch duplicate order_id (tie-breaker and version update test)
        dup_ord_winner = (base_time + timedelta(days=100)).isoformat()
        order_rows.append(["ORD-00001", valid_cust_ids[0], "2026-02-15", "125.50", "COMPLETED", dup_ord_winner])

        # 2. Negative amount
        order_rows.append(["ORD-00991", valid_cust_ids[1], "2026-02-10", "-50.00", "COMPLETED", base_time.isoformat()])

        # 3. Invalid status
        order_rows.append(["ORD-00992", valid_cust_ids[2], "2026-02-11", "75.00", "UNKNOWN_STATUS", base_time.isoformat()])

        # 4. Orphan foreign key (customer_id does not exist)
        order_rows.append(["ORD-00993", "CUST-NONEXISTENT-9999", "2026-02-12", "120.00", "PENDING", base_time.isoformat()])

        # 5. Invalid date format
        order_rows.append(["ORD-00994", valid_cust_ids[3], "2026/02/31", "200.00", "PROCESSING", base_time.isoformat()])

        # Write orders CSV
        with open(orders_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["order_id", "customer_id", "order_date", "amount", "status", "updated_at"])
            for r in order_rows:
                writer.writerow(r)
            # Write 1 malformed order row (only 3 fields instead of 6)
            writer.writerow(["ORD-00999", valid_cust_ids[0], "2026-02-20"])

        return customers_path, orders_path

    def generate_benchmark_datasets(
        self, output_dir: Path | str, customer_count: int = 100000, order_count: int = 100000
    ) -> Tuple[Path, Path]:
        """Generates large-scale datasets for high-throughput performance benchmarking."""
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        customers_path = out_path / f"customers_{customer_count}.csv"
        orders_path = out_path / f"orders_{order_count}.csv"

        cities = ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia", "San Antonio", "San Diego"]
        statuses = list(ALLOWED_ORDER_STATUSES)
        base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        # Write customers streaming
        with open(customers_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["customer_id", "customer_name", "email", "city", "created_at"])
            for i in range(1, customer_count + 1):
                cid = f"CUST-{i:07d}"
                name = f"Customer_{i}"
                email = f"user_{i}@benchmark-domain.com"
                city = cities[i % len(cities)]
                created_at = (base_time + timedelta(seconds=i)).isoformat()
                writer.writerow([cid, name, email, city, created_at])

        # Write orders streaming
        with open(orders_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["order_id", "customer_id", "order_date", "amount", "status", "updated_at"])
            for i in range(1, order_count + 1):
                oid = f"ORD-{i:07d}"
                # Refer to valid customer
                cid = f"CUST-{(i % customer_count) + 1:07d}"
                order_date = "2026-03-01"
                amount = f"{(10.0 + (i % 500) + 0.99):.2f}"
                status = statuses[i % len(statuses)]
                updated_at = (base_time + timedelta(seconds=i + 3600)).isoformat()
                writer.writerow([oid, cid, order_date, amount, status, updated_at])

        return customers_path, orders_path
