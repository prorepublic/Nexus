"""Deterministic fake worker.

Lets the entire orchestration loop (plan -> dispatch -> execute -> validate ->
repair -> complete) run end to end with zero model usage. Behavior is driven
by directives embedded in the task instruction so tests stay deterministic:

- ``[fake:fail]``            always fail
- ``[fake:fail-first=N]``    fail the first N attempts, then succeed
- ``[fake:write=relpath]``   write a marker file at relpath in the workspace
- ``[fake:sleep=S]``         sleep S seconds (cancellation/timeout tests)
"""

import re
import threading
import time

from nexus.domain.enums import TaskKind, WorkerName
from nexus.workers.base import (
    EventCallback,
    TaskSpec,
    WorkerAdapter,
    WorkerCapabilities,
    WorkerEvent,
    WorkerHealth,
    WorkerResult,
)

_DIRECTIVE = re.compile(r"\[fake:([a-z-]+)(?:=([^\]]+))?\]")


class FakeWorker(WorkerAdapter):
    name = WorkerName.FAKE

    def __init__(self) -> None:
        self._attempts: dict[str, int] = {}  # task_id -> attempts seen
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    def health_check(self) -> WorkerHealth:
        return WorkerHealth(
            name=self.name,
            installed=True,
            version="builtin",
            authenticated=True,
            detail="deterministic in-process worker (no model usage)",
        )

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(kinds=frozenset(TaskKind), structured_output=True)

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            self._cancelled.add(run_id)
        return True

    def execute(self, spec: TaskSpec, on_event: EventCallback | None = None) -> WorkerResult:
        events: list[WorkerEvent] = []

        def emit(event: WorkerEvent) -> None:
            events.append(event)
            if on_event:
                on_event(event)

        emit(WorkerEvent(type="started", message=f"fake worker attempt on {spec.task_id}"))
        directives = dict(_DIRECTIVE.findall(spec.instruction))

        if "sleep" in directives:
            deadline = time.monotonic() + float(directives["sleep"])
            while time.monotonic() < deadline:
                with self._lock:
                    if spec.run_id in self._cancelled:
                        emit(WorkerEvent(type="cancelled", message="cancel requested"))
                        return WorkerResult(
                            ok=False,
                            summary="cancelled",
                            error_category="cancelled",
                            events=events,
                            session_ref=f"fake-{spec.run_id}",
                        )
                time.sleep(0.05)

        with self._lock:
            attempt = self._attempts.get(spec.task_id, 0) + 1
            self._attempts[spec.task_id] = attempt

        fail = "fail" in directives
        if "fail-first" in directives:
            fail = attempt <= int(directives["fail-first"])

        if not fail and not spec.read_only:
            relpath = directives.get("write", "FAKE_WORKER_RESULT.md")
            target = (spec.workspace / relpath).resolve()
            if spec.workspace.resolve() not in target.parents:
                emit(WorkerEvent(type="error", message="write path escapes workspace"))
                return WorkerResult(
                    ok=False,
                    summary="policy violation: path escape",
                    error_category="policy",
                    events=events,
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f"Fake worker output for {spec.task_id}, attempt {attempt}.\n"
                f"Instruction (first 200 chars): {spec.instruction[:200]}\n"
            )
            emit(WorkerEvent(type="tool", message=f"wrote {relpath}"))

        emit(WorkerEvent(type="result", message="failed" if fail else "succeeded"))
        return WorkerResult(
            ok=not fail,
            summary=(
                f"fake worker attempt {attempt}: "
                + ("simulated failure" if fail else "completed deterministically")
            ),
            session_ref=f"fake-{spec.run_id}",
            exit_code=1 if fail else 0,
            error_category="simulated" if fail else None,
            events=events,
            output_text="fake-worker attempt "
            f"{attempt} {'failed' if fail else 'succeeded'} for {spec.task_id}",
        )
