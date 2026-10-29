"""Structured logging configuration for YottaFlex Data Pipeline."""

from __future__ import annotations

import contextvars
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Context variable to hold active run_id across concurrent or nested calls
current_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_run_id", default="NO_RUN_ID")


class StructuredLogFormatter(logging.Formatter):
    """Formats log records as structured key-value lines or JSON."""

    def __init__(self, as_json: bool = False):
        super().__init__()
        self.as_json = as_json

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now(timezone.utc).isoformat()
        run_id = getattr(record, "run_id", None) or current_run_id.get()

        data: Dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "run_id": run_id,
            "message": record.getMessage(),
        }

        # Include exception info if present
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)

        if self.as_json:
            return json.dumps(data)

        # Human-readable structured format
        msg = f"[{timestamp}] [{record.levelname:<7}] [run_id={run_id}] [{record.name}] {record.getMessage()}"
        if "exception" in data:
            msg += f"\n{data['exception']}"
        return msg


def setup_logger(
    name: str = "pipeline",
    log_level: int = logging.INFO,
    log_file: Optional[Path | str] = None,
    json_file: bool = False,
) -> logging.Logger:
    """Configures and returns a logger instance with console and optional file handlers."""
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # Avoid duplicate handlers if setup_logger is called multiple times
    if logger.handlers:
        return logger

    # Console Handler (Human-readable structured text)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(StructuredLogFormatter(as_json=False))
    logger.addHandler(console_handler)

    # Optional File Handler
    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(file_path), encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(StructuredLogFormatter(as_json=json_file))
        logger.addHandler(file_handler)

    return logger
