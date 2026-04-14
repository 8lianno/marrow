"""Structlog setup. JSON logs to runs/<slug>/logs/run.jsonl, console for human use."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import structlog


def configure(level: str = "INFO", run_log_path: Path | None = None) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if run_log_path is not None:
        run_log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(run_log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        handlers.append(file_handler)

    logging.basicConfig(level=log_level, handlers=handlers, force=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_secrets,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


_SECRET_KEYS = {"api_key", "anthropic_api_key", "jina_api_key", "authorization", "token"}


def _redact_secrets(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict.keys()):
        if key.lower() in _SECRET_KEYS:
            event_dict[key] = "***REDACTED***"
    return event_dict


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
