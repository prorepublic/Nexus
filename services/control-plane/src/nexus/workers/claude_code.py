"""Claude Code CLI adapter.

Uses the officially installed, subscription-authenticated Claude Code CLI in
non-interactive mode (`claude -p`). Verified against claude 2.1.x:
- `--output-format stream-json` for machine-readable events
- `--allowedTools` for explicit permission rules
- `--add-dir` restricted to the assigned workspace
- `--max-turns` for finite autonomy
- never uses --dangerously-skip-permissions

The adapter degrades gracefully: if the installed CLI rejects a flag, the
run fails with a structured 'cli-flags' error instead of corrupting state.
"""

import shutil
import subprocess
import threading

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

READ_ONLY_TOOLS = "Read Grep Glob"
WRITE_TOOLS = 'Read Grep Glob Edit Write "Bash(git status)" "Bash(git diff *)" "Bash(git log *)"'


class ClaudeCodeAdapter(WorkerAdapter):
    name = WorkerName.CLAUDE_CODE

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or get_settings().claude_bin
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()

    # -- health -----------------------------------------------------------
    def health_check(self) -> WorkerHealth:
        path = shutil.which(self.binary)
        if path is None:
            return WorkerHealth(
                name=self.name,
                installed=False,
                detail="claude CLI not found on PATH. Install: npm install -g "
                "@anthropic-ai/claude-code, then run `claude` once to log in.",
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
                detail=f"claude --version failed: {exc}",
            )
        # Authentication cannot be verified without spending a model turn, so it
        # is reported as unknown rather than assumed (evidence-based reporting).
        return WorkerHealth(
            name=self.name,
            installed=True,
            version=version,
            authenticated=None,
            detail="installed; authentication is verified on first live run "
            "(`nexus worker test claude-code`)",
        )

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            kinds=frozenset(
                {
                    TaskKind.PLANNING,
                    TaskKind.ARCHITECTURE,
                    TaskKind.REVIEW,
                    TaskKind.SECURITY_REVIEW,
                    TaskKind.DOCUMENTATION,
                    TaskKind.IMPLEMENTATION,
                    TaskKind.TESTING,
                    TaskKind.REFACTORING,
                    TaskKind.MAINTENANCE,
                }
            ),
            structured_output=True,
        )

    # -- execution --------------------------------------------------------
    def build_argv(self, spec: TaskSpec) -> list[str]:
        tools = READ_ONLY_TOOLS if spec.read_only else WRITE_TOOLS
        return [
            self.binary,
            "-p",
            spec.instruction,
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            str(spec.max_turns),
            "--allowedTools",
            tools,
            "--add-dir",
            str(spec.workspace),
            "--no-session-persistence",
        ]

    def execute(self, spec: TaskSpec, on_event: EventCallback | None = None) -> WorkerResult:
        events: list[WorkerEvent] = []

        def emit(event: WorkerEvent) -> None:
            events.append(event)
            if on_event:
                on_event(event)

        argv = self.build_argv(spec)
        emit(
            WorkerEvent(
                type="started",
                message="claude -p (stream-json)",
                metadata={
                    "max_turns": spec.max_turns,
                    "read_only": spec.read_only,
                },
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
                summary=f"failed to launch claude: {exc}",
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
        """Translate stream-json output into the neutral WorkerResult."""
        session_ref: str | None = None
        final_text = ""
        is_error = returncode != 0
        for obj in iter_jsonl(stdout):
            obj_type = str(obj.get("type", ""))
            session_ref = obj.get("session_id", session_ref)
            if obj_type == "assistant":
                content = obj.get("message", {}).get("content", [])
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        emit(WorkerEvent(type="tool", message=str(block.get("name", "tool"))))
            elif obj_type == "result":
                final_text = str(obj.get("result", "") or "")
                is_error = bool(obj.get("is_error", False)) or is_error
                emit(
                    WorkerEvent(
                        type="result",
                        message="error" if is_error else "ok",
                        metadata={"num_turns": obj.get("num_turns")},
                    )
                )

        error_category = None
        stderr_l = stderr.lower()
        if is_error:
            if "log in" in stderr_l or "unauthorized" in stderr_l or "authentication" in stderr_l:
                error_category = "auth"
            elif "rate" in stderr_l or "limit" in stderr_l:
                error_category = "limit"
            elif "unknown option" in stderr_l or "unknown argument" in stderr_l:
                error_category = "cli-flags"
            else:
                error_category = "unknown"

        summary = final_text.strip()[:2000] if final_text else redact_text(stderr.strip()[:500])
        return WorkerResult(
            ok=not is_error,
            summary=summary or ("succeeded" if not is_error else "failed"),
            session_ref=session_ref,
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
