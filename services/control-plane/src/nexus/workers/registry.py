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
        return {name for name, health in self.all_health().items() if health.available}


_registry: WorkerRegistry | None = None


def get_registry() -> WorkerRegistry:
    global _registry
    if _registry is None:
        _registry = WorkerRegistry()
    return _registry
