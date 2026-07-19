"""Codex CLI adapter.

Uses the officially installed, ChatGPT-authenticated Codex CLI in
non-interactive mode (`codex exec`):
- `--json` for JSONL events
- `--sandbox workspace-write` for implementation, `--sandbox read-only` for
  planning and review
- `--cd <workspace>` to constrain the working directory
- never `danger-full-access` (also refused by the worker-codex execution
  profile)

All process execution flows through nexus.execution (ADR-006). The parser
handles both the wrapped {"msg": {...}} and flat event shapes observed across
codex versions; `nexus worker test codex-cli --live` verifies the installed
CLI before first production use.
"""

import shutil
from typing import Any

from nexus.config import get_settings
from nexus.domain.enums import TaskKind, WorkerName
from nexus.execution import get_profile, get_runner
from nexus.observability import get_logger, redact_text
from nexus.workers.base import (
    EventCallback,
    TaskSpec,
    WorkerAdapter,
    WorkerCapabilities,
    WorkerEvent,
    WorkerHealth,
    WorkerResult,
    iter_jsonl,
)

log = get_logger(__name__)


class CodexCliAdapter(WorkerAdapter):
    name = WorkerName.CODEX_CLI

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or get_settings().codex_bin

    def health_check(self) -> WorkerHealth:
        path = shutil.which(self.binary)
        if path is None:
            return WorkerHealth(
                name=self.name,
                installed=False,
                detail="codex CLI not found on PATH. `nexus worker doctor` can "
                "install it (npm install -g @openai/codex); login requires the "
                "owner's ChatGPT account.",
            )
        runner = get_runner()
        health_profile = get_profile("worker-health-readonly")
        cache_dir = get_settings().cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        version_result = runner.run(health_profile, [self.binary, "--version"], cwd=cache_dir)
        version = version_result.stdout.strip() or None
        if not version_result.ok:
            return WorkerHealth(
                name=self.name,
                installed=True,
                authenticated=None,
                detail=f"codex --version failed: {version_result.stderr.strip()[:200]}",
            )
        authenticated: bool | None = None
        detail = "installed"
        login = runner.run(
            get_profile("worker-auth-status"), [self.binary, "login", "status"], cwd=cache_dir
        )
        combined = (login.stdout + login.stderr).lower()
        if login.exit_code == 0 and "logged in" in combined:
            authenticated = True
            detail = "installed and authenticated"
        elif "not logged in" in combined:
            authenticated = False
            detail = "installed but not logged in (run `codex login`)"
        return WorkerHealth(
            name=self.name,
            installed=True,
            version=version,
            authenticated=authenticated,
            detail=detail,
        )

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            kinds=frozenset(
                {
                    TaskKind.IMPLEMENTATION,
                    TaskKind.TESTING,
                    TaskKind.REFACTORING,
                    TaskKind.MAINTENANCE,
                    TaskKind.REVIEW,
                    TaskKind.PLANNING,
                    TaskKind.DOCUMENTATION,
                }
            ),
            structured_output=True,
        )

    def build_argv(self, spec: TaskSpec) -> list[str]:
        sandbox = "read-only" if spec.read_only else "workspace-write"
        return [
            self.binary,
            "exec",
            "--json",
            "--sandbox",
            sandbox,
            "--cd",
            str(spec.workspace),
            "--skip-git-repo-check",
            "-",  # read the task instruction from stdin, never argv
        ]

    def execute(self, spec: TaskSpec, on_event: EventCallback | None = None) -> WorkerResult:
        events: list[WorkerEvent] = []

        def emit(event: WorkerEvent) -> None:
            events.append(event)
            if on_event:
                on_event(event)

        health = self.health_check()
        if not health.installed:
            return WorkerResult(
                ok=False, summary=health.detail, error_category="not-installed", events=events
            )

        emit(
            WorkerEvent(
                type="started",
                message="codex exec --json",
                metadata={"read_only": spec.read_only},
            )
        )

        def stream_line(line: str) -> None:
            for obj in iter_jsonl(line):
                self._emit_stream_event(obj, emit)

        result = get_runner().run(
            get_profile("worker-codex"),
            self.build_argv(spec),
            cwd=spec.workspace,
            permitted_roots=[spec.workspace],
            timeout=spec.timeout_seconds,
            on_line=stream_line,
            cancel_key=spec.run_id,
            run_id=spec.run_id,
            stdin_data=spec.instruction,
        )
        if result.error:
            return WorkerResult(
                ok=False,
                summary=f"failed to launch codex: {result.error}",
                error_category="crash",
                events=events,
            )
        if result.cancelled:
            emit(WorkerEvent(type="cancelled", message="cancel requested"))
            return WorkerResult(
                ok=False, summary="cancelled", error_category="cancelled", events=events
            )
        if result.timed_out:
            emit(WorkerEvent(type="error", message="timeout"))
            return WorkerResult(
                ok=False,
                summary=f"timed out after {spec.timeout_seconds}s",
                error_category="timeout",
                events=events,
                output_text=result.stdout[-4000:],
            )
        parsed = self.parse_result(
            result.stdout, result.stderr, result.exit_code or 0, [], lambda event: None
        )
        parsed.events = events
        return parsed

    @staticmethod
    def _payload(obj: dict[str, Any]) -> dict[str, Any]:
        msg = obj.get("msg")
        return msg if isinstance(msg, dict) else obj

    def _emit_stream_event(self, obj: dict[str, Any], emit: EventCallback) -> None:
        payload = self._payload(obj)
        obj_type = str(payload.get("type", ""))
        if obj_type in {"exec_command_begin", "command_execution"}:
            emit(WorkerEvent(type="command", message=str(payload.get("command", ""))[:200]))
        elif obj_type in {"error", "turn.failed"}:
            emit(WorkerEvent(type="error", message=str(payload.get("message", ""))[:500]))

    def parse_result(
        self,
        stdout: str,
        stderr: str,
        returncode: int,
        events: list[WorkerEvent],
        emit: EventCallback,
    ) -> WorkerResult:
        """Translate codex exec JSONL into the neutral WorkerResult."""
        thread_ref: str | None = None
        final_text = ""
        usage: dict[str, Any] = {}
        for obj in iter_jsonl(stdout):
            payload = self._payload(obj)
            obj_type = str(payload.get("type", ""))
            thread_ref = (
                obj.get("thread_id")
                or payload.get("thread_id")
                or payload.get("session_id")
                or thread_ref
            )
            if obj_type in {"agent_message", "item.completed"}:
                raw_item = payload.get("item", payload)
                item: dict[str, Any] = raw_item if isinstance(raw_item, dict) else {}
                text = item.get("text") or item.get("message") or ""
                if text:
                    final_text = str(text)
            elif obj_type in {"exec_command_begin", "command_execution"}:
                emit(WorkerEvent(type="command", message=str(payload.get("command", ""))[:200]))
            elif obj_type in {"error", "turn.failed"}:
                emit(WorkerEvent(type="error", message=str(payload.get("message", ""))[:500]))
            elif obj_type in {"turn.completed", "token_count"}:
                reported = payload.get("usage") or payload.get("info") or {}
                if isinstance(reported, dict) and reported:
                    usage = reported

        is_error = returncode != 0
        error_category = None
        stderr_l = stderr.lower()
        if is_error:
            if "login" in stderr_l or "auth" in stderr_l:
                error_category = "auth"
            elif "rate limit" in stderr_l or "usage" in stderr_l:
                error_category = "limit"
            elif "unexpected argument" in stderr_l or "unrecognized" in stderr_l:
                error_category = "cli-flags"
            else:
                error_category = "unknown"

        summary = final_text.strip()[:2000] if final_text else redact_text(stderr.strip()[:500])
        return WorkerResult(
            ok=not is_error,
            summary=summary or ("succeeded" if not is_error else "failed"),
            session_ref=thread_ref,
            exit_code=returncode,
            error_category=error_category,
            events=events,
            output_text=redact_text(final_text[-8000:]),
            usage=usage,
        )

    def cancel(self, run_id: str) -> bool:
        return get_runner().cancel(run_id)
