"""Provider-independent worker contract (ADR-002).

Nexus domain logic never touches Claude-specific or Codex-specific output;
adapters translate CLI behavior into these neutral structures.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus.domain.enums import TaskKind, WorkerName


@dataclass(frozen=True)
class WorkerHealth:
    name: WorkerName
    installed: bool
    version: str | None = None
    authenticated: bool | None = None  # None = cannot be determined cheaply
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.installed and self.authenticated is not False


@dataclass(frozen=True)
class WorkerCapabilities:
    kinds: frozenset[TaskKind]
    non_interactive: bool = True
    structured_output: bool = False
    supports_cancel: bool = True


@dataclass(frozen=True)
class TaskSpec:
    """What a worker needs to know to execute one bounded unit of work."""

    task_id: str
    run_id: str
    kind: TaskKind
    instruction: str
    workspace: Path
    timeout_seconds: int = 1800
    max_turns: int = 30
    read_only: bool = False  # planning/review runs must not write
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerEvent:
    type: str  # started|message|tool|command|result|error|cancelled
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerResult:
    ok: bool
    summary: str
    session_ref: str | None = None
    exit_code: int | None = None
    error_category: str | None = None  # timeout|auth|limit|crash|policy|unknown
    events: list[WorkerEvent] = field(default_factory=list)
    output_text: str = ""
    # CLI-reported usage where officially available (turns, tokens, duration).
    # Never fabricated; empty when the CLI does not report it.
    usage: dict[str, Any] = field(default_factory=dict)


EventCallback = Callable[[WorkerEvent], None]


class WorkerAdapter(ABC):
    name: WorkerName

    @abstractmethod
    def health_check(self) -> WorkerHealth: ...

    @abstractmethod
    def capabilities(self) -> WorkerCapabilities: ...

    def estimate_suitability(self, kind: TaskKind) -> float:
        """0.0 (unsuitable) to 1.0 (ideal). Default: capability membership."""
        return 1.0 if kind in self.capabilities().kinds else 0.2

    def prepare_workspace(self, spec: TaskSpec) -> None:
        """Hook for provider-specific workspace setup. Default: nothing."""
        spec.workspace.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def execute(self, spec: TaskSpec, on_event: EventCallback | None = None) -> WorkerResult:
        """Run the task to completion (bounded by spec.timeout_seconds)."""

    def cancel(self, run_id: str) -> bool:
        """Best-effort cancellation of a running task. Default: unsupported."""
        return False


def iter_jsonl(text: str) -> Iterator[dict[str, Any]]:
    """Parse JSON-lines output defensively; skip non-JSON lines."""
    import json

    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj
