"""The single process execution engine (ADR-006).

Responsibilities:
- profile enforcement (operation-level command schemas, prohibited flags)
- cwd confinement AND path-argument confinement inside permitted roots
- filtered environment (no ambient secrets reach children)
- sensitive input (worker prompts) via stdin, never argv — logs and audit
  records only ever see a sanitized argv summary plus a stdin hash/size
- streaming stdout with per-line callbacks (JSONL-friendly)
- timeout and cancellation that terminate the WHOLE process group
- output caps, secret redaction, structured audit events
"""

import hashlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nexus.execution.paths import PathEscapeError, confine_cwd, confine_path
from nexus.execution.profiles import ExecutionProfile, ProfileViolation, sanitize_argv
from nexus.observability import get_logger, redact_text

log = get_logger(__name__)

LineCallback = Callable[[str], None]
# recorder(profile_name, safe_argv, cwd, exit_code, duration_ms, truncated, run_id)
AuditRecorder = Callable[[str, list[str], str, int | None, int, bool, str | None], None]


def _confine_path_arguments(profile: ExecutionProfile, argv: list[str], roots: list[Path]) -> None:
    """Typed path-argument enforcement.

    - values of declared path flags (--add-dir, --cd, -C-style overrides are
      instead prohibited outright) must resolve inside a permitted root;
    - under confine_path_args, any token containing a '..' path component is
      refused, and absolute-path positionals must resolve inside a permitted
      root — a safe cwd can never be combined with an escaping file argument.
    """
    from nexus.execution.profiles import parse_argv_tail

    parsed = parse_argv_tail(argv[1:], profile.all_value_flags)

    def _confine(value: str, what: str) -> None:
        last: PathEscapeError | None = None
        for root in roots:
            try:
                confine_path(value, root)
                return
            except PathEscapeError as exc:
                last = exc
        raise ProfileViolation(
            f"[{profile.name}] {what} escapes permitted roots: "
            f"{value[:60]} ({last.reason if last else 'no roots'})"
        )

    for flag, value in parsed.flag_values:
        if flag in profile.path_value_flags:
            _confine(value, f"path flag {flag}")

    if profile.confine_path_args:
        for token in argv[1:]:
            candidate = token.split("=", 1)[1] if token.startswith("-") and "=" in token else token
            if ".." in Path(candidate).parts:
                raise ProfileViolation(
                    f"[{profile.name}] parent traversal in argument: {candidate[:60]}"
                )
        for positional in parsed.positionals:
            if positional.startswith("/"):
                _confine(positional, "absolute path argument")


@dataclass
class ExecutionResult:
    profile: str
    argv: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False
    timed_out: bool = False
    cancelled: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled


@dataclass
class _ActiveProcess:
    popen: subprocess.Popen[str]
    cancelled: bool = False


class ProcessRunner:
    def __init__(self, recorder: AuditRecorder | None = None) -> None:
        self._recorder = recorder
        self._active: dict[str, _ActiveProcess] = {}
        self._lock = threading.Lock()

    # -- cancellation -------------------------------------------------------
    def cancel(self, key: str) -> bool:
        with self._lock:
            active = self._active.get(key)
            if active is None:
                return False
            active.cancelled = True
            popen = active.popen
        _terminate_group(popen)
        return True

    def active_keys(self) -> list[str]:
        with self._lock:
            return list(self._active)

    # -- execution ----------------------------------------------------------
    def run(
        self,
        profile: ExecutionProfile,
        argv: list[str],
        cwd: Path,
        *,
        permitted_roots: list[Path] | None = None,
        timeout: int | None = None,
        on_line: LineCallback | None = None,
        cancel_key: str | None = None,
        env_extra: dict[str, str] | None = None,
        run_id: str | None = None,
        stdin_data: str | None = None,
    ) -> ExecutionResult:
        if not profile.executables:
            # e.g. validation-untrusted: nothing may execute on the host
            return ExecutionResult(
                profile=profile.name,
                argv=sanitize_argv(profile, argv),
                exit_code=None,
                stdout="",
                stderr="",
                duration_ms=0,
                error=f"profile {profile.name} permits no host execution"
                + (
                    f"; approval gate: {profile.approval_action}" if profile.approval_action else ""
                ),
            )
        profile.check(argv)
        safe_argv = sanitize_argv(profile, argv)
        roots = [root.resolve() for root in (permitted_roots or [cwd])]
        if permitted_roots:
            cwd = confine_cwd(cwd, permitted_roots)
        cwd = cwd.resolve()
        _confine_path_arguments(profile, argv, roots)

        env = {key: os.environ[key] for key in profile.env_passthrough if key in os.environ}
        if env_extra:
            env.update(env_extra)

        stdin_digest = hashlib.sha256(stdin_data.encode()).hexdigest()[:16] if stdin_data else None
        effective_timeout = timeout or profile.timeout_seconds
        start = time.monotonic()
        try:
            popen = subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=env,
                text=True,
                stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,  # own process group: kills reach children
            )
        except OSError as exc:
            return ExecutionResult(
                profile=profile.name,
                argv=safe_argv,
                exit_code=None,
                stdout="",
                stderr="",
                duration_ms=int((time.monotonic() - start) * 1000),
                error=str(exc),
            )

        if stdin_data is not None:

            def _feed_stdin() -> None:
                try:
                    assert popen.stdin is not None
                    popen.stdin.write(stdin_data)
                    popen.stdin.close()
                except (BrokenPipeError, OSError):
                    pass  # child exited early; its exit code tells the story

            threading.Thread(target=_feed_stdin, daemon=True).start()

        active = _ActiveProcess(popen=popen)
        if cancel_key:
            with self._lock:
                self._active[cancel_key] = active

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        stdout_size = 0
        truncated = False

        def _pump_stdout() -> None:
            nonlocal stdout_size, truncated
            assert popen.stdout is not None
            for line in popen.stdout:
                remaining = profile.max_output_bytes - stdout_size
                if remaining > 0:
                    if len(line) > remaining:
                        stdout_chunks.append(line[:remaining] + "\n[TRUNCATED]\n")
                        truncated = True
                    else:
                        stdout_chunks.append(line)
                    stdout_size += len(line)
                else:
                    truncated = True
                if on_line is not None:
                    try:
                        on_line(line.rstrip("\n"))
                    except Exception:  # callbacks must never kill the pump
                        log.exception("execution.on_line_callback_failed")

        def _pump_stderr() -> None:
            nonlocal truncated
            assert popen.stderr is not None
            size = 0
            for line in popen.stderr:
                if size < profile.max_output_bytes:
                    stderr_chunks.append(line)
                    size += len(line)
                else:
                    truncated = True

        threads = [
            threading.Thread(target=_pump_stdout, daemon=True),
            threading.Thread(target=_pump_stderr, daemon=True),
        ]
        for thread in threads:
            thread.start()

        timed_out = False
        try:
            popen.wait(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_group(popen)
        finally:
            for thread in threads:
                thread.join(timeout=10)
            if cancel_key:
                with self._lock:
                    self._active.pop(cancel_key, None)

        duration_ms = int((time.monotonic() - start) * 1000)
        result = ExecutionResult(
            profile=profile.name,
            # Only the sanitized summary survives in results, logs, and audit:
            # raw argv (which may carry flag values) is never persisted.
            argv=safe_argv,
            exit_code=popen.returncode,
            stdout=redact_text("".join(stdout_chunks)),
            stderr=redact_text("".join(stderr_chunks)),
            duration_ms=duration_ms,
            truncated=truncated,
            timed_out=timed_out,
            cancelled=active.cancelled,
        )
        log.info(
            "execution.completed",
            profile=profile.name,
            argv=safe_argv,
            cwd=str(cwd),
            exit_code=result.exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
            cancelled=active.cancelled,
            stdin_sha256=stdin_digest,
            stdin_bytes=len(stdin_data) if stdin_data else None,
        )
        if self._recorder is not None:
            try:
                self._recorder(
                    profile.name,
                    safe_argv + ([f"stdin:sha256:{stdin_digest}"] if stdin_digest else []),
                    str(cwd),
                    result.exit_code,
                    duration_ms,
                    truncated,
                    run_id,
                )
            except Exception:
                log.exception("execution.audit_record_failed")
        return result


def _terminate_group(popen: subprocess.Popen[str]) -> None:
    """SIGTERM the whole process group, escalate to SIGKILL."""
    try:
        pgid = os.getpgid(popen.pid)
    except (ProcessLookupError, PermissionError):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    for _ in range(20):  # up to ~2s of grace
        if popen.poll() is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _db_recorder(
    profile_name: str,
    safe_argv: list[str],
    cwd: str,
    exit_code: int | None,
    duration_ms: int,
    truncated: bool,
    run_id: str | None,
) -> None:
    """Persist a CommandExecution audit row. Best-effort: audit failures are
    logged, never raised into the execution path. safe_argv is the sanitized
    summary produced by the runner — raw prompts and flag values never reach
    this function."""
    from nexus.db.base import session_scope
    from nexus.db.models import CommandExecution, Run

    with session_scope() as session:
        # ad-hoc executions (health checks, live smoke tests) may carry a
        # run key that has no Run row; audit them without the FK
        if run_id is not None and session.get(Run, run_id) is None:
            run_id = None
        session.add(
            CommandExecution(
                run_id=run_id,
                argv=[redact_text(token) for token in safe_argv],
                cwd=cwd,
                exit_code=exit_code,
                duration_ms=duration_ms,
                output_truncated=truncated,
                policy_decision=f"allowed:{profile_name}",
            )
        )


def spawn_detached(argv: list[str], cwd: Path, log_file: Path) -> int:
    """Start Nexus's OWN control-plane process detached from the caller.

    This is service self-management, not task execution: the child is our own
    CLI entry point with a fixed argv shape, it outlives the caller by design,
    and its output goes to the managed log file. It still runs in its own
    session (process group) so `nexus stop` can terminate the whole tree.
    """
    allowed_modules = {"nexus.cli.main"}
    if len(argv) < 3 or argv[1] != "-m" or argv[2] not in allowed_modules:
        raise ValueError("spawn_detached only launches the Nexus CLI module")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "ab") as sink:
        child = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdout=sink,
            stderr=sink,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return child.pid


_runner: ProcessRunner | None = None


def get_runner() -> ProcessRunner:
    """Process-wide runner with database audit recording."""
    global _runner
    if _runner is None:
        _runner = ProcessRunner(recorder=_db_recorder)
    return _runner
