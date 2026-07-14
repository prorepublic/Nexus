"""Rules-based worker routing (docs/WORKER-ROUTING.md).

Defaults (overridable per goal or task):
- Claude Code: architecture, planning, cross-file reasoning, review, security,
  documentation synthesis.
- Codex CLI: implementation, tests, refactoring, repetitive changes, build fixes.
- Cross-review: the reviewer must differ from the implementer when another
  worker is available.
"""

from dataclasses import dataclass, field

from nexus.domain.enums import TaskKind, WorkerName

DEFAULT_PREFERENCES: dict[TaskKind, list[WorkerName]] = {
    TaskKind.PLANNING: [WorkerName.CLAUDE_CODE, WorkerName.CODEX_CLI],
    TaskKind.ARCHITECTURE: [WorkerName.CLAUDE_CODE, WorkerName.CODEX_CLI],
    TaskKind.REVIEW: [WorkerName.CLAUDE_CODE, WorkerName.CODEX_CLI],
    TaskKind.SECURITY_REVIEW: [WorkerName.CLAUDE_CODE, WorkerName.CODEX_CLI],
    TaskKind.DOCUMENTATION: [WorkerName.CLAUDE_CODE, WorkerName.CODEX_CLI],
    TaskKind.IMPLEMENTATION: [WorkerName.CODEX_CLI, WorkerName.CLAUDE_CODE],
    TaskKind.TESTING: [WorkerName.CODEX_CLI, WorkerName.CLAUDE_CODE],
    TaskKind.REFACTORING: [WorkerName.CODEX_CLI, WorkerName.CLAUDE_CODE],
    TaskKind.MAINTENANCE: [WorkerName.CODEX_CLI, WorkerName.CLAUDE_CODE],
}


@dataclass
class RoutingDecision:
    worker: WorkerName
    reviewer: WorkerName | None
    reason: str


@dataclass
class Router:
    available: set[WorkerName] = field(default_factory=set)
    preferences: dict[TaskKind, list[WorkerName]] = field(
        default_factory=lambda: dict(DEFAULT_PREFERENCES)
    )

    def route(
        self,
        kind: TaskKind,
        requested_worker: WorkerName | None = None,
    ) -> RoutingDecision:
        if not self.available:
            raise NoWorkerAvailable("no workers are available")

        if requested_worker is not None:
            if requested_worker not in self.available:
                raise NoWorkerAvailable(f"requested worker '{requested_worker}' is unavailable")
            worker = requested_worker
            reason = "owner override"
        else:
            ranked = self.preferences.get(kind, [WorkerName.CLAUDE_CODE, WorkerName.CODEX_CLI])
            worker = next((w for w in ranked if w in self.available), None) or next(
                iter(sorted(self.available))
            )
            reason = f"default preference for {kind}"

        reviewer = self._pick_reviewer(worker)
        return RoutingDecision(worker=worker, reviewer=reviewer, reason=reason)

    def _pick_reviewer(self, implementer: WorkerName) -> WorkerName | None:
        """Cross-agent review: never make a worker the sole reviewer of its own
        output. The fake worker is not a meaningful reviewer for real workers."""
        candidates = [
            w
            for w in self.available
            if w != implementer and not (w == WorkerName.FAKE and implementer != WorkerName.FAKE)
        ]
        if not candidates:
            return None
        # Prefer Claude for review when it is not the implementer.
        if WorkerName.CLAUDE_CODE in candidates:
            return WorkerName.CLAUDE_CODE
        return sorted(candidates)[0]


class NoWorkerAvailable(Exception):
    pass
