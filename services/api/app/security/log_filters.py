"""Structured logging that cannot be used to exfiltrate a field value.

Service code never passes document text to the logger, but "never" is a habit
rather than a guarantee. This filter is the backstop: anything attached under a
suspicious key is replaced before a handler formats it, and record arguments are
length-capped so a stray f-string cannot dump a document body into stdout.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

REDACTED = "[redacted]"
MAX_VALUE_LENGTH = 200

_SENSITIVE_KEY = re.compile(
    r"(value|plaintext|raw|secret|token|cookie|password|guess|comparison|ciphertext|nonce|key)",
    re.IGNORECASE,
)

SAFE_EXTRA_KEYS = frozenset(
    {
        "event",
        "session_id",
        "document_ref",
        "field_ref",
        "request_ref",
        "event_type",
        "outcome",
        "category",
        "status_code",
        "duration_ms",
        "detector",
        "count",
    }
)

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


def _cap(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
        return value[:MAX_VALUE_LENGTH] + "…"
    return value


class SensitiveFieldFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in list(record.__dict__.items()):
            if key in _RESERVED:
                continue
            if _SENSITIVE_KEY.search(key) or key not in SAFE_EXTRA_KEYS:
                record.__dict__[key] = REDACTED
            elif isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
                record.__dict__[key] = value[:MAX_VALUE_LENGTH] + "…"
        if isinstance(record.msg, str) and len(record.msg) > MAX_VALUE_LENGTH:
            record.msg = record.msg[:MAX_VALUE_LENGTH] + "…"
        # Arguments are capped rather than dropped, so uvicorn access lines stay
        # readable while an oversized value cannot be dumped through a format arg.
        if isinstance(record.args, tuple):
            record.args = tuple(_cap(item) for item in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _cap(value) for key, value in record.args.items()}
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in SAFE_EXTRA_KEYS:
            if key in record.__dict__ and record.__dict__[key] != REDACTED:
                payload[key] = record.__dict__[key]
        if record.exc_info:
            # Type only. A traceback can quote a local variable holding plaintext.
            payload["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SensitiveFieldFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False

    # Model downloads log every hub request at INFO. That noise buries the events
    # an operator actually needs to see.
    for name in ("httpx", "httpcore", "filelock", "huggingface_hub"):
        logging.getLogger(name).setLevel(logging.WARNING)
