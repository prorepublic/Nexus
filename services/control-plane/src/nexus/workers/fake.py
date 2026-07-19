"""Deterministic fake worker.

Lets the entire orchestration loop (plan -> dispatch -> execute -> validate ->
repair -> complete) run end to end with zero model usage. Behavior is driven
by directives embedded in the task instruction so tests stay deterministic:

- ``[fake:fail]``            always fail
- ``[fake:fail-first=N]``    fail the first N attempts, then succeed
- ``[fake:write=relpath]``   write a marker file at relpath in the workspace
- ``[fake:sleep=S]``         sleep S seconds (cancellation/timeout tests)
- ``[fake:review=X]``        as a reviewer, return a structured verdict:
                             approved | changes | changes-once | blocked | garbage
- ``[fake:plan=N]``          as a planner, return a structured N-task plan
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

    def _structured_review(
        self, spec: TaskSpec, mode: str, events: list[WorkerEvent], emit
    ) -> WorkerResult:
        """Deterministic reviewer verdicts for testing the review lifecycle."""
        import json

        with self._lock:
            reviews_seen = self._attempts.get(f"review:{spec.task_id}", 0) + 1
            self._attempts[f"review:{spec.task_id}"] = reviews_seen

        payload: dict[str, object]
        if mode == "garbage":
            output = "I could not produce JSON, sorry."
        else:
            request_changes = mode == "changes" or (mode == "changes-once" and reviews_seen == 1)
            if mode == "blocked":
                payload = {"verdict": "blocked", "summary": "cannot review", "findings": []}
            elif request_changes:
                payload = {
                    "verdict": "changes-requested",
                    "summary": "simulated blocking finding",
                    "findings": [
                        {
                            "severity": "high",
                            "category": "correctness",
                            "description": "Simulated defect: marker file must contain "
                            "the word 'repaired'.",
                            "file": "FAKE_WORKER_RESULT.md",
                            "blocking": True,
                            "recommendation": "Rewrite the marker file.",
                        }
                    ],
                }
            else:
                payload = {"verdict": "approved", "summary": "looks good", "findings": []}
            output = json.dumps(payload)
        emit(WorkerEvent(type="result", message="review emitted"))
        return WorkerResult(
            ok=True,
            summary="fake review",
            session_ref=f"fake-review-{spec.run_id}",
            exit_code=0,
            events=events,
            output_text=output,
        )

    def _structured_plan(
        self, spec: TaskSpec, task_count: int, events: list[WorkerEvent], emit
    ) -> WorkerResult:
        """Deterministic structured plan for testing live-planner plumbing."""
        import json

        payload = {
            "objective": "Fake objective",
            "assumptions": ["deterministic fake plan"],
            "exclusions": [],
            "risks": [],
            "affected_components": [],
            "definition_of_done": "all tasks complete",
            "tasks": [
                {
                    "title": f"Fake task {index + 1}",
                    "kind": "implementation",
                    "instruction": f"Do fake step {index + 1}. [fake:write=step{index + 1}.md]",
                    "depends_on": [index - 1] if index > 0 else [],
                    "risk": "low",
                    "validations": ["files-exist"],
                    "expected_files": [f"step{index + 1}.md"],
                }
                for index in range(task_count)
            ],
        }
        emit(WorkerEvent(type="result", message="plan emitted"))
        return WorkerResult(
            ok=True,
            summary="fake plan",
            session_ref=f"fake-plan-{spec.run_id}",
            exit_code=0,
            events=events,
            output_text=json.dumps(payload),
        )

    def execute(self, spec: TaskSpec, on_event: EventCallback | None = None) -> WorkerResult:
        events: list[WorkerEvent] = []

        def emit(event: WorkerEvent) -> None:
            events.append(event)
            if on_event:
                on_event(event)

        emit(WorkerEvent(type="started", message=f"fake worker attempt on {spec.task_id}"))
        directives = dict(_DIRECTIVE.findall(spec.instruction))

        if spec.kind == TaskKind.REVIEW and "review" in directives:
            return self._structured_review(spec, directives["review"], events, emit)
        if spec.kind == TaskKind.PLANNING and "plan" in directives:
            return self._structured_plan(spec, int(directives["plan"]), events, emit)

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
