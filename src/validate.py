"""Validation engine for structural schema validation and domain business rules."""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from src.config import DatasetConfig, FieldRule
from src.extract import ExtractedRecord
from src.quarantine import QuarantineManager, QuarantineRecord

logger = logging.getLogger(__name__)


def parse_iso8601(val: str) -> Optional[datetime]:
    """Parses an ISO-8601 timestamp string into a timezone-aware or naive datetime."""
    clean = val.strip()
    if not clean:
        return None
    # Normalize trailing Z to UTC offset for compatibility
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(clean)
    except Exception:
        return None


class SchemaValidator:
    """Validates structural constraints: missing columns, nullability, and basic types."""

    def __init__(self, config: DatasetConfig):
        self.config = config

    def validate(
        self,
        records: List[ExtractedRecord],
        run_id: str,
        source_file: str,
        quarantine_mgr: QuarantineManager,
    ) -> Tuple[List[ExtractedRecord], List[QuarantineRecord]]:
        valid_records: List[ExtractedRecord] = []

        for record in records:
            row_num = record.row_number
            raw = record.data
            rejection_reason = None
            rejection_code = None

            # 1. Check required fields and nullability
            for field_name, rule in self.config.fields.items():
                val = raw.get(field_name)

                # Check if required column was not present in the record dict
                if rule.required and val is None:
                    rejection_code = "MISSING_REQUIRED_COLUMN"
                    rejection_reason = f"Required column '{field_name}' is missing"
                    break

                # Check non-nullable fields with empty values
                if not rule.nullable:
                    if val is None or val.strip() == "" or val.strip().lower() in ("null", "none"):
                        rejection_code = "EMPTY_REQUIRED_FIELD"
                        rejection_reason = f"Field '{field_name}' cannot be empty or null"
                        break

                # Basic type castability checks if value is present
                if val is not None and val.strip() != "":
                    cleaned_val = val.strip()
                    if rule.data_type == "int":
                        try:
                            int(cleaned_val)
                        except ValueError:
                            rejection_code = "INVALID_DATA_TYPE"
                            rejection_reason = f"Field '{field_name}' value '{cleaned_val}' is not a valid integer"
                            break

                    elif rule.data_type == "float":
                        try:
                            f_val = float(cleaned_val)
                            if not math.isfinite(f_val):
                                rejection_code = "NON_FINITE_NUMERIC"
                                rejection_reason = f"Field '{field_name}' has non-finite value '{cleaned_val}'"
                                break
                        except ValueError:
                            rejection_code = "INVALID_DATA_TYPE"
                            rejection_reason = f"Field '{field_name}' value '{cleaned_val}' is not a valid float"
                            break

                    elif rule.data_type in ("datetime", "date"):
                        parsed_dt = parse_iso8601(cleaned_val)
                        if parsed_dt is None:
                            rejection_code = "INVALID_TIMESTAMP_FORMAT"
                            rejection_reason = (
                                f"Field '{field_name}' value '{cleaned_val}' is not a valid ISO-8601 timestamp"
                            )
                            break

            if rejection_code:
                quarantine_mgr.add_rejection(
                    run_id=run_id,
                    dataset_name=self.config.name,
                    source_file=source_file,
                    source_row_number=row_num,
                    raw_payload=record.raw_payload,
                    rejection_stage="SCHEMA_VALIDATION",
                    rejection_code=rejection_code,
                    rejection_reason=rejection_reason,
                )
            else:
                valid_records.append(record)

        return valid_records, quarantine_mgr.records


class BusinessValidator:
    """Validates domain-specific business rules and referential integrity."""

    def __init__(self, config: DatasetConfig):
        self.config = config

    def validate(
        self,
        records: List[ExtractedRecord],
        run_id: str,
        source_file: str,
        quarantine_mgr: QuarantineManager,
        parent_keys: Optional[Set[str]] = None,
    ) -> Tuple[List[ExtractedRecord], List[QuarantineRecord]]:
        valid_records: List[ExtractedRecord] = []

        for record in records:
            row_num = record.row_number
            raw = record.data
            rejection_reason = None
            rejection_code = None

            for field_name, rule in self.config.fields.items():
                val = raw.get(field_name, "").strip()

                # If optional field is empty, business rules for value content don't apply
                if rule.nullable and not val:
                    continue

                # 1. Regex validation (e.g., email format)
                if rule.regex_pattern and not rule.regex_pattern.match(val):
                    rejection_code = f"INVALID_{field_name.upper()}_FORMAT"
                    rejection_reason = f"Field '{field_name}' with value '{val}' failed format rule"
                    break

                # 2. Allowed values (enum check, e.g. status)
                if rule.allowed_values and val not in rule.allowed_values:
                    rejection_code = f"INVALID_{field_name.upper()}_VALUE"
                    rejection_reason = (
                        f"Field '{field_name}' value '{val}' is not in allowed set: "
                        f"{sorted(list(rule.allowed_values))}"
                    )
                    break

                # 3. Numeric bounds (e.g. non-negative amount)
                if rule.data_type in ("int", "float"):
                    try:
                        num_val = float(val)
                        if rule.min_value is not None and num_val < rule.min_value:
                            rejection_code = "NEGATIVE_NUMERIC_VALUE"
                            rejection_reason = (
                                f"Field '{field_name}' value {num_val} is less than minimum {rule.min_value}"
                            )
                            break
                        if rule.max_value is not None and num_val > rule.max_value:
                            rejection_code = "EXCEEDED_MAX_VALUE"
                            rejection_reason = (
                                f"Field '{field_name}' value {num_val} exceeds maximum {rule.max_value}"
                            )
                            break
                    except ValueError:
                        # Should have been caught in schema validation, but guard defensively
                        rejection_code = "INVALID_NUMERIC_VALUE"
                        rejection_reason = f"Field '{field_name}' value '{val}' is not numeric"
                        break

            # 4. Referential Integrity validation
            if not rejection_code and parent_keys is not None and "customer_id" in self.config.foreign_keys:
                cust_id = raw.get("customer_id", "").strip()
                if cust_id not in parent_keys:
                    rejection_code = "ORPHAN_FOREIGN_KEY"
                    rejection_reason = (
                        f"Referential integrity failure: customer_id '{cust_id}' does not exist in customers table"
                    )

            if rejection_code:
                quarantine_mgr.add_rejection(
                    run_id=run_id,
                    dataset_name=self.config.name,
                    source_file=source_file,
                    source_row_number=row_num,
                    raw_payload=record.raw_payload,
                    rejection_stage="BUSINESS_VALIDATION",
                    rejection_code=rejection_code,
                    rejection_reason=rejection_reason,
                )
            else:
                valid_records.append(record)

        return valid_records, quarantine_mgr.records
