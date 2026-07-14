"""Codex CLI adapter.

Uses the officially installed, ChatGPT-authenticated Codex CLI in
non-interactive mode (`codex exec`). Documented behavior targeted:
- `codex exec --json` for JSONL events
- `--sandbox workspace-write` for implementation, `--sandbox read-only` for
  planning and review
- `--cd <workspace>` to constrain the working directory
- never uses `--sandbox danger-full-access`

Note: Codex CLI is NOT currently installed on this machine. The adapter is
implemented against the documented interface and covered by fixture-based
parser tests; health_check reports the real installation state. Before the
first live run, `nexus worker test codex-cli` verifies the installed CLI's
flags and adjusts nothing silently — mismatches surface as 'cli-flags' errors.
"""

import shutil
import subprocess
import threading
from typing import Any

from nexus.config import get_settings
from nexus.domain.enums import TaskKind, WorkerName
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
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()

    def health_check(self) -> WorkerHealth:
        path = shutil.which(self.binary)
        if path is None:
            return WorkerHealth(
                name=self.name,
                installed=False,
                detail="codex CLI not found on PATH. Install: npm install -g "
                "@openai/codex, then `codex login` (ChatGPT Plus account).",
            )
        try:
            proc = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=20, check=False
            )
            version = proc.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired) as exc:
            return WorkerHealth(
                name=self.name,
                installed=True,
                authenticated=None,
                detail=f"codex --version failed: {exc}",
            )
        authenticated: bool | None = None
        detail = "installed"
        try:
            login = subprocess.run(
                [path, "login", "status"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            combined = (login.stdout + login.stderr).lower()
            if login.returncode == 0 and "logged in" in combined:
                authenticated = True
                detail = "installed and authenticated"
            elif "not logged in" in combined:
                authenticated = False
                detail = "installed but not logged in (run `codex login`)"
        except (OSError, subprocess.TimeoutExpired):
            pass
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
            spec.instruction,
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

        argv = self.build_argv(spec)
        emit(
            WorkerEvent(
                type="started", message="codex exec --json", metadata={"read_only": spec.read_only}
            )
        )
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(spec.workspace),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            return WorkerResult(
                ok=False,
                summary=f"failed to launch codex: {exc}",
                error_category="crash",
                events=events,
            )
        with self._lock:
            self._procs[spec.run_id] = proc
        try:
            stdout, stderr = proc.communicate(timeout=spec.timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            emit(WorkerEvent(type="error", message="timeout"))
            return WorkerResult(
                ok=False,
                summary=f"timed out after {spec.timeout_seconds}s",
                error_category="timeout",
                events=events,
                output_text=redact_text(stdout[-4000:]),
            )
        finally:
            with self._lock:
                self._procs.pop(spec.run_id, None)

        return self.parse_result(stdout, stderr, proc.returncode, events, emit)

    def parse_result(
        self,
        stdout: str,
        stderr: str,
        returncode: int,
        events: list[WorkerEvent],
        emit: EventCallback,
    ) -> WorkerResult:
        """Translate codex exec JSONL into the neutral WorkerResult.

        Handles both event shapes seen across codex versions:
        {"msg": {"type": "...", ...}} and flat {"type": "...", ...}.
        """
        thread_ref: str | None = None
        final_text = ""
        for obj in iter_jsonl(stdout):
            msg = obj.get("msg")
            payload: dict[str, Any] = msg if isinstance(msg, dict) else obj
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
        )

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            proc = self._procs.get(run_id)
        if proc is None:
            return False
        proc.terminate()
        return True
