"""Claude Code CLI adapter.

Uses the officially installed, subscription-authenticated Claude Code CLI in
non-interactive mode (`claude -p`). Verified against claude 2.1.x:
- `--output-format stream-json` for machine-readable events
- `--allowedTools` for explicit permission rules
- `--add-dir` restricted to the assigned workspace
- `--max-turns` for finite autonomy
- never uses --dangerously-skip-permissions (also refused by the
  worker-claude execution profile, so a regression cannot slip through)

All process execution flows through nexus.execution (ADR-006): streaming
JSONL events, process-group termination, env filtering, audit records.
"""

import json
import shutil
from pathlib import Path

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

READ_ONLY_TOOLS = "Read Grep Glob"
WRITE_TOOLS = 'Read Grep Glob Edit Write "Bash(git status)" "Bash(git diff *)" "Bash(git log *)"'


class ClaudeCodeAdapter(WorkerAdapter):
    name = WorkerName.CLAUDE_CODE

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or get_settings().claude_bin

    # -- health -----------------------------------------------------------
    def health_check(self) -> WorkerHealth:
        if shutil.which(self.binary) is None:
            return WorkerHealth(
                name=self.name,
                installed=False,
                detail="claude CLI not found on PATH. Install: npm install -g "
                "@anthropic-ai/claude-code, then run `claude` once to log in.",
            )
        result = get_runner().run(
            get_profile("worker-health-readonly"),
            [self.binary, "--version"],
            cwd=get_settings().cache_dir,
        )
        version = result.stdout.strip() or None
        if not result.ok:
            return WorkerHealth(
                name=self.name,
                installed=True,
                authenticated=None,
                detail=f"claude --version failed: {result.stderr.strip()[:200]}",
            )
        # Cheap standalone-credential detection without spending a model turn:
        # claude stores CLI credentials in ~/.claude/.credentials.json or the
        # macOS keychain ("Claude Code-credentials"). An interactive desktop
        # session does NOT give the standalone CLI credentials, so absence of
        # both means non-interactive runs will fail auth.
        creds_file = Path.home() / ".claude" / ".credentials.json"
        has_credentials = creds_file.exists()
        if not has_credentials:
            keychain = get_runner().run(
                get_profile("worker-health-readonly"),
                ["security", "find-generic-password", "-s", "Claude Code-credentials"],
                cwd=get_settings().cache_dir,
            )
            has_credentials = keychain.ok
        if not has_credentials:
            return WorkerHealth(
                name=self.name,
                installed=True,
                version=version,
                authenticated=False,
                detail="installed but not logged in for standalone use: run "
                "`claude` in a terminal once and use /login with the Claude "
                "Max account",
            )
        # Credentials exist; validity is confirmed on first live run.
        return WorkerHealth(
            name=self.name,
            installed=True,
            version=version,
            authenticated=None,
            detail="installed with stored credentials; verified on first live "
            "run (`nexus worker test claude-code --live`)",
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
        # The task instruction is delivered via STDIN (see execute), never argv:
        # argv appears in process listings and sanitized logs; prompts must not.
        argv = [
            self.binary,
            "-p",
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
        schema = spec.context.get("json_schema")
        if schema:
            argv += ["--json-schema", json.dumps(schema)]
        return argv

    def execute(self, spec: TaskSpec, on_event: EventCallback | None = None) -> WorkerResult:
        events: list[WorkerEvent] = []

        def emit(event: WorkerEvent) -> None:
            events.append(event)
            if on_event:
                on_event(event)

        emit(
            WorkerEvent(
                type="started",
                message="claude -p (stream-json)",
                metadata={"max_turns": spec.max_turns, "read_only": spec.read_only},
            )
        )

        def stream_line(line: str) -> None:
            for obj in iter_jsonl(line):
                self._emit_stream_event(obj, emit)

        result = get_runner().run(
            get_profile("worker-claude"),
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
                summary=f"failed to launch claude: {result.error}",
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
    def _emit_stream_event(obj: dict, emit: EventCallback) -> None:
        obj_type = str(obj.get("type", ""))
        if obj_type == "assistant":
            for block in obj.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    emit(WorkerEvent(type="tool", message=str(block.get("name", "tool"))))
        elif obj_type == "result":
            is_error = bool(obj.get("is_error", False))
            emit(
                WorkerEvent(
                    type="result",
                    message="error" if is_error else "ok",
                    metadata={"num_turns": obj.get("num_turns")},
                )
            )

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
        usage: dict[str, object] = {}
        is_error = returncode != 0
        for obj in iter_jsonl(stdout):
            obj_type = str(obj.get("type", ""))
            session_ref = obj.get("session_id", session_ref)
            if obj_type == "assistant":
                for block in obj.get("message", {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        emit(WorkerEvent(type="tool", message=str(block.get("name", "tool"))))
            elif obj_type == "result":
                final_text = str(obj.get("result", "") or "")
                is_error = bool(obj.get("is_error", False)) or is_error
                usage = {
                    "num_turns": obj.get("num_turns"),
                    "duration_ms": obj.get("duration_ms"),
                }
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
            usage=usage,
        )

    def cancel(self, run_id: str) -> bool:
        return get_runner().cancel(run_id)
