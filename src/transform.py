"""Data transformation, enrichment, and deterministic deduplication."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.config import DatasetConfig
from src.extract import ExtractedRecord
from src.validate import parse_iso8601

logger = logging.getLogger(__name__)


def format_iso_utc(dt: datetime) -> str:
    """Formats a datetime object as canonical ISO-8601 UTC with Z suffix."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class DataTransformer:
    """Transforms raw valid records into normalized, enriched representations."""

    def __init__(self, config: DatasetConfig):
        self.config = config

    def transform_record(
        self, record: ExtractedRecord, source_file: str, ingestion_timestamp: str
    ) -> Dict[str, Any]:
        """Applies field-level normalization and derived attributes."""
        raw = record.data
        transformed: Dict[str, Any] = {}

        for field_name, rule in self.config.fields.items():
            val = raw.get(field_name)

            if val is None or val.strip() == "":
                transformed[field_name] = None
                continue

            cleaned = val.strip()

            if rule.data_type == "string":
                # Special normalization for emails: lowercase
                if field_name == "email":
                    transformed[field_name] = cleaned.lower()
                elif field_name == "status":
                    transformed[field_name] = cleaned.upper()
                else:
                    transformed[field_name] = cleaned

            elif rule.data_type == "int":
                transformed[field_name] = int(cleaned)

            elif rule.data_type == "float":
                transformed[field_name] = round(float(cleaned), 2)

            elif rule.data_type in ("datetime", "date"):
                dt = parse_iso8601(cleaned)
                if dt:
                    transformed[field_name] = format_iso_utc(dt)
                else:
                    transformed[field_name] = cleaned

        # Derived fields
        if self.config.name == "orders" and "amount" in transformed:
            amt = transformed["amount"]
            transformed["amount_cents"] = int(round(amt * 100)) if amt is not None else 0

        # Operational metadata columns
        transformed["_ingested_at"] = ingestion_timestamp
        transformed["_source_file"] = source_file
        transformed["_version"] = 1

        return transformed

    def deduplicate_in_batch(
        self, records: List[Tuple[ExtractedRecord, Dict[str, Any]]]
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Deterministic deduplication of records with identical primary keys in the same batch.

        Conflict resolution policy:
        1. Compare version column (updated_at / created_at). The record with the later timestamp wins.
        2. Tie-breaker: If timestamps are equal, the record appearing later in the source file
           (higher source_row_number) wins.
        """
        pk_field = self.config.primary_key
        ver_field = self.config.version_col

        grouped: Dict[str, List[Tuple[ExtractedRecord, Dict[str, Any]]]] = {}

        for extracted_rec, trans_rec in records:
            pk = trans_rec.get(pk_field)
            if pk not in grouped:
                grouped[pk] = []
            grouped[pk].append((extracted_rec, trans_rec))

        deduplicated: List[Dict[str, Any]] = []
        duplicates_resolved_count = 0

        for pk, candidates in grouped.items():
            if len(candidates) == 1:
                deduplicated.append(candidates[0][1])
            else:
                duplicates_resolved_count += (len(candidates) - 1)

                def sort_key(item: Tuple[ExtractedRecord, Dict[str, Any]]) -> Tuple[str, int]:
                    rec_extracted, rec_trans = item
                    timestamp_val = str(rec_trans.get(ver_field) or "")
                    row_num = rec_extracted.row_number
                    return (timestamp_val, row_num)

                # Sort ascending, last item is the winner
                sorted_candidates = sorted(candidates, key=sort_key)
                winner = sorted_candidates[-1][1]
                logger.info(
                    "In-batch duplicate resolved for %s %s='%s': %d duplicates collapsed into row %d",
                    self.config.name,
                    pk_field,
                    pk,
                    len(candidates),
                    sorted_candidates[-1][0].row_number,
                )
                deduplicated.append(winner)

        return deduplicated, duplicates_resolved_count

    def transform_all(
        self, records: List[ExtractedRecord], source_file: str
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Transforms all valid extracted records and performs deterministic in-batch deduplication."""
        now_utc = format_iso_utc(datetime.now(timezone.utc))

        transformed_pairs = [
            (rec, self.transform_record(rec, source_file, now_utc))
            for rec in records
        ]

        deduped_records, dups_resolved = self.deduplicate_in_batch(transformed_pairs)
        return deduped_records, dups_resolved
