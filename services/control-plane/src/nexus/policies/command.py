"""Command execution policy and sandboxed executor.

All subprocess execution in Nexus flows through CommandExecutor:
- argv lists only (no shell strings, no interpolation of untrusted text);
- allowlisted executables;
- destructive-pattern detection;
- enforced working directory inside the permitted workspace;
- timeout and output-size caps;
- filtered environment (no ambient secrets leak into child processes);
- full audit of argv, exit code, and duration.
"""

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from nexus.observability import get_logger, redact_text

log = get_logger(__name__)

DEFAULT_ALLOWED_EXECUTABLES: frozenset[str] = frozenset(
    {
        "git",
        "gh",
        "claude",
        "codex",
        "python",
        "python3",
        "uv",
        "pytest",
        "ruff",
        "mypy",
        "alembic",
        "pip-audit",
        "node",
        "npm",
        "npx",
        "tsc",
        "make",
        "docker",
        "ls",
        "cat",
        "mkdir",
        "touch",
        "echo",
    }
)

# argv patterns that are refused regardless of allowlist membership.
DESTRUCTIVE_PATTERNS: list[list[str]] = [
    ["git", "push", "--force"],
    ["git", "push", "-f"],
    ["git", "reset", "--hard"],
    ["git", "clean", "-fd"],
    ["rm", "-rf"],
    ["docker", "system", "prune"],
]

# Environment variables allowed to pass through to child processes.
ENV_PASSTHROUGH = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "USER",
    "SHELL",
    "NODE_OPTIONS",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)


class CommandPolicyError(Exception):
    pass


@dataclass(frozen=True)
class CommandPolicy:
    allowed_executables: frozenset[str] = DEFAULT_ALLOWED_EXECUTABLES
    workspace: Path | None = None  # commands must run at or under this directory
    timeout_seconds: int = 600
    max_output_bytes: int = 512_000
    extra_env: dict[str, str] = field(default_factory=dict)

    def check(self, argv: list[str], cwd: Path) -> None:
        if not argv:
            raise CommandPolicyError("empty command")
        if any(not isinstance(a, str) for a in argv):
            raise CommandPolicyError("argv must be a list of strings")
        executable = Path(argv[0]).name
        if executable not in self.allowed_executables:
            raise CommandPolicyError(f"executable not allowlisted: {executable}")
        for pattern in DESTRUCTIVE_PATTERNS:
            if _matches_prefix(argv, pattern):
                raise CommandPolicyError(f"destructive command refused: {' '.join(pattern)}")
        resolved = cwd.resolve()
        if self.workspace is not None:
            ws = self.workspace.resolve()
            if not (resolved == ws or ws in resolved.parents):
                raise CommandPolicyError(f"cwd {resolved} escapes permitted workspace {ws}")


def _matches_prefix(argv: list[str], pattern: list[str]) -> bool:
    """True if pattern's tokens all appear at the start of argv (flags in any position
    for the final token, so `git push origin --force` is still caught)."""
    if len(argv) < len(pattern):
        return False
    head, tail = pattern[:-1], pattern[-1]
    if argv[: len(head)] != head:
        return False
    return tail in argv[len(head) :]


@dataclass
class CommandResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class CommandExecutor:
    def __init__(self, policy: CommandPolicy) -> None:
        self.policy = policy

    def _child_env(self) -> dict[str, str]:
        env = {key: os.environ[key] for key in ENV_PASSTHROUGH if key in os.environ}
        env.update(self.policy.extra_env)
        return env

    def run(self, argv: list[str], cwd: Path, timeout: int | None = None) -> CommandResult:
        self.policy.check(argv, cwd)
        effective_timeout = timeout or self.policy.timeout_seconds
        start = time.monotonic()
        timed_out = False
        try:
            completed = subprocess.run(  # noqa: PLW1510
                argv,
                cwd=str(cwd),
                env=self._child_env(),
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
            )
            stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            exit_code, timed_out = -1, True
        duration_ms = int((time.monotonic() - start) * 1000)

        truncated = False
        cap = self.policy.max_output_bytes
        if len(stdout) > cap:
            stdout, truncated = stdout[:cap] + "\n[TRUNCATED]", True
        if len(stderr) > cap:
            stderr, truncated = stderr[:cap] + "\n[TRUNCATED]", True

        log.info(
            "command.executed",
            argv=argv,
            cwd=str(cwd),
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
        )
        return CommandResult(
            argv=argv,
            exit_code=exit_code,
            stdout=redact_text(stdout),
            stderr=redact_text(stderr),
            duration_ms=duration_ms,
            truncated=truncated,
            timed_out=timed_out,
        )
