"""Worker registry: single place the orchestrator resolves adapters from."""

from nexus.domain.enums import WorkerName
from nexus.workers.base import WorkerAdapter, WorkerHealth
from nexus.workers.claude_code import ClaudeCodeAdapter
from nexus.workers.codex_cli import CodexCliAdapter
from nexus.workers.fake import FakeWorker


class WorkerRegistry:
    def __init__(self, adapters: dict[WorkerName, WorkerAdapter] | None = None) -> None:
        self._adapters: dict[WorkerName, WorkerAdapter] = adapters or {
            WorkerName.FAKE: FakeWorker(),
            WorkerName.CLAUDE_CODE: ClaudeCodeAdapter(),
            WorkerName.CODEX_CLI: CodexCliAdapter(),
        }

    def get(self, name: WorkerName | str) -> WorkerAdapter:
        key = WorkerName(name)
        if key not in self._adapters:
            raise KeyError(f"unknown worker: {name}")
        return self._adapters[key]

    def all_health(self) -> dict[WorkerName, WorkerHealth]:
        return {name: adapter.health_check() for name, adapter in self._adapters.items()}

    def available_names(self) -> set[WorkerName]:
        """Installed+authenticated workers minus any the owner disabled."""
        disabled = disabled_worker_names()
        return {
            name
            for name, health in self.all_health().items()
            if health.available and str(name) not in disabled
        }


def disabled_worker_names() -> set[str]:
    """Workers the owner disabled via `nexus worker disable` (DB flag).
    Fails open to empty when the database is unavailable (health checks still
    gate actual execution)."""
    try:
        from sqlalchemy import select

        from nexus.db.base import session_scope
        from nexus.db.models import Worker

        with session_scope() as session:
            return {
                worker.name
                for worker in session.scalars(select(Worker).where(Worker.enabled.is_(False)))
            }
    except Exception:
        return set()


_registry: WorkerRegistry | None = None


def get_registry() -> WorkerRegistry:
    global _registry
    if _registry is None:
        _registry = WorkerRegistry()
    return _registry
