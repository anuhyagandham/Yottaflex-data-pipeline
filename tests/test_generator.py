"""Tests for synthetic dataset generator and seed reproducibility."""

from __future__ import annotations

from pathlib import Path

from src.generator import SyntheticDataGenerator


def test_generator_deterministic_seed(temp_dir: Path) -> None:
    dir1 = temp_dir / "gen1"
    dir2 = temp_dir / "gen2"

    g1 = SyntheticDataGenerator(seed=42)
    c1, o1 = g1.generate_sample_datasets(dir1)

    g2 = SyntheticDataGenerator(seed=42)
    c2, o2 = g2.generate_sample_datasets(dir2)

    assert c1.read_text(encoding="utf-8") == c2.read_text(encoding="utf-8")
    assert o1.read_text(encoding="utf-8") == o2.read_text(encoding="utf-8")


def test_generator_sample_contains_injected_edge_cases(temp_dir: Path) -> None:
    gen = SyntheticDataGenerator(seed=42)
    c_csv, o_csv = gen.generate_sample_datasets(temp_dir)

    c_content = c_csv.read_text(encoding="utf-8")
    o_content = o_csv.read_text(encoding="utf-8")

    # Customers edge cases
    assert "loyalty_tier" in c_content  # Extra column
    assert "bad-email" in c_content or "not-an-email" in c_content  # Invalid email
    assert "invalid-2026" in c_content  # Invalid date

    # Orders edge cases
    assert "-50.00" in o_content  # Negative amount
    assert "UNKNOWN_STATUS" in o_content  # Invalid status
    assert "CUST-NONEXISTENT" in o_content  # Orphan customer FK


def test_generator_benchmark_smoke(temp_dir: Path) -> None:
    gen = SyntheticDataGenerator(seed=42)
    c_csv, o_csv = gen.generate_benchmark_datasets(temp_dir, customer_count=100, order_count=100)

    assert c_csv.exists()
    assert o_csv.exists()
    assert len(c_csv.read_text(encoding="utf-8").strip().splitlines()) == 101  # Header + 100
    assert len(o_csv.read_text(encoding="utf-8").strip().splitlines()) == 101
