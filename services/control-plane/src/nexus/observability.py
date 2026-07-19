"""Structured logging and secret redaction.

Every important Nexus action emits a structured event (see nexus.services.events
for persisted run events). This module configures process logging and provides
the redaction filter applied to anything that could contain secret material.
"""

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

# Patterns for values that must never reach logs or persisted events.
_SECRET_PATTERNS = [
    re.compile(r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"ntn_[A-Za-z0-9]{20,}"),  # Notion internal integration tokens
    re.compile(r"secret_[A-Za-z0-9]{20,}"),  # legacy Notion tokens
    re.compile(r"sk-[A-Za-z0-9-]{20,}"),  # API keys (Anthropic/OpenAI style)
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),  # JWTs
]

_SECRET_KEY_HINTS = ("token", "secret", "password", "api_key", "apikey", "authorization")

REDACTED = "[REDACTED]"


def redact_text(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact_value(key: str, value: Any) -> Any:
    if any(hint in key.lower() for hint in _SECRET_KEY_HINTS):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    return value


def redact_mapping(data: MutableMapping[str, Any] | dict[str, Any]) -> dict[str, Any]:
    return {key: redact_value(key, value) for key, value in data.items()}


def _redaction_processor(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        event_dict[key] = redact_value(key, event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    logging.basicConfig(stream=sys.stderr, level=level.upper(), format="%(message)s")
    renderer: Any
    if json_logs:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redaction_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
