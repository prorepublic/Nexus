"""Persisted, redacted event recording for observability and audit."""

from typing import Any

from sqlalchemy.orm import Session

from nexus.db.models import AuditEvent, RunEvent
from nexus.observability import redact_mapping, redact_text

MAX_EVENT_MESSAGE = 4000


def record_run_event(
    session: Session,
    run_id: str,
    type_: str,
    message: str = "",
    status: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        RunEvent(
            run_id=run_id,
            type=type_,
            status=status,
            message=redact_text(message)[:MAX_EVENT_MESSAGE],
            metadata_=redact_mapping(metadata or {}),
        )
    )


def record_audit(
    session: Session,
    event_type: str,
    *,
    actor: str = "system",
    correlation_id: str | None = None,
    goal_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            event_type=event_type,
            actor=actor,
            correlation_id=correlation_id,
            goal_id=goal_id,
            task_id=task_id,
            run_id=run_id,
            status=status,
            metadata_=redact_mapping(metadata or {}),
        )
    )
