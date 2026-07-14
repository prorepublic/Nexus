"""Purpose-specific execution profiles (ADR-006).

Each profile narrowly defines what one class of Nexus-initiated subprocess may
do: which executable, which subcommands, which flags are refused regardless of
position, where it may run, which environment it receives, its timeout and
output budget, and whether it needs an approval gate first.

Network policy is documented per profile but not kernel-enforced on macOS
without extra tooling; see docs/THREAT-MODEL.md for the honest residual risk.
"""

from dataclasses import dataclass

DEFAULT_ENV = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "USER",
    "SHELL",
)

NODE_ENV = (*DEFAULT_ENV, "NODE_OPTIONS")
GIT_ENV = (
    *DEFAULT_ENV,
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)


class ProfileViolation(Exception):
    pass


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    executables: frozenset[str]
    # First non-flag token after the executable must be in this set (None = any).
    allowed_subcommands: frozenset[str] | None = None
    # Two-token verbs like ("pr", "view"); when set, the first two non-flag
    # tokens must match one of the pairs. Checked in addition to subcommands.
    allowed_verb_pairs: frozenset[tuple[str, str]] | None = None
    # Tokens refused anywhere in argv, regardless of order or position.
    prohibited_tokens: frozenset[str] = frozenset()
    env_passthrough: tuple[str, ...] = DEFAULT_ENV
    timeout_seconds: int = 300
    max_output_bytes: int = 512_000
    writes_allowed: bool = False
    network: str = "none-expected"  # documented intent, not kernel-enforced
    approval_action: str | None = None  # approval kind required before running
    description: str = ""

    def check(self, argv: list[str]) -> None:
        if not argv or any(not isinstance(token, str) for token in argv):
            raise ProfileViolation(f"[{self.name}] argv must be a non-empty list of strings")
        from pathlib import Path

        executable = Path(argv[0]).name
        if executable not in self.executables:
            raise ProfileViolation(f"[{self.name}] executable not permitted: {executable}")
        lowered = [token.lower() for token in argv[1:]]
        for token in lowered:
            if token in self.prohibited_tokens:
                raise ProfileViolation(f"[{self.name}] prohibited token: {token}")
            # catch --flag=value forms of prohibited flags
            base = token.split("=", 1)[0]
            if base in self.prohibited_tokens:
                raise ProfileViolation(f"[{self.name}] prohibited token: {base}")
        positional = [token for token in argv[1:] if not token.startswith("-")]
        if self.allowed_subcommands is not None:
            first = positional[0] if positional else (argv[1] if len(argv) > 1 else "")
            if first not in self.allowed_subcommands:
                raise ProfileViolation(
                    f"[{self.name}] subcommand not permitted: {first or '(none)'}"
                )
        if self.allowed_verb_pairs is not None:
            pair = tuple(positional[:2])
            if len(pair) < 2 or pair not in self.allowed_verb_pairs:
                raise ProfileViolation(
                    f"[{self.name}] operation not permitted: {' '.join(pair) or '(none)'}"
                )


_GIT_DESTRUCTIVE = frozenset(
    {
        "--force",
        "-f",
        "--force-with-lease",
        "--force-if-includes",
        "--hard",
        "--mirror",
        "--delete",
        "-d",
        "--amend",
        "--no-verify",
    }
)

PROFILES: dict[str, ExecutionProfile] = {
    profile.name: profile
    for profile in [
        ExecutionProfile(
            name="health-readonly",
            executables=frozenset(
                {"git", "gh", "docker", "node", "npm", "python3", "uv", "claude", "codex", "psql"}
            ),
            allowed_subcommands=frozenset(
                {"--version", "-v", "-V", "auth", "login", "info", "version", "config"}
            ),
            timeout_seconds=30,
            max_output_bytes=64_000,
            description="Version and authentication checks only.",
        ),
        ExecutionProfile(
            name="worker-claude",
            executables=frozenset({"claude"}),
            prohibited_tokens=frozenset(
                {"--dangerously-skip-permissions", "--allow-dangerously-skip-permissions"}
            ),
            env_passthrough=NODE_ENV,
            timeout_seconds=1800,
            max_output_bytes=2_000_000,
            writes_allowed=True,
            network="required (Anthropic API via subscription)",
            description="Non-interactive Claude Code runs inside a task workspace.",
        ),
        ExecutionProfile(
            name="worker-codex",
            executables=frozenset({"codex"}),
            allowed_subcommands=frozenset({"exec", "login"}),
            prohibited_tokens=frozenset(
                {"danger-full-access", "--dangerously-bypass-approvals-and-sandbox", "--yolo"}
            ),
            env_passthrough=NODE_ENV,
            timeout_seconds=1800,
            max_output_bytes=2_000_000,
            writes_allowed=True,
            network="required (OpenAI API via subscription)",
            description="Non-interactive Codex runs inside a task workspace.",
        ),
        ExecutionProfile(
            name="git-clone",
            executables=frozenset({"git"}),
            allowed_subcommands=frozenset({"clone", "fetch"}),
            prohibited_tokens=_GIT_DESTRUCTIVE,
            env_passthrough=GIT_ENV,
            timeout_seconds=600,
            writes_allowed=True,
            network="required (git remote)",
            description="Cloning a repository during onboarding.",
        ),
        ExecutionProfile(
            name="git-readonly",
            executables=frozenset({"git"}),
            allowed_subcommands=frozenset(
                {
                    "status",
                    "diff",
                    "log",
                    "rev-parse",
                    "show",
                    "ls-files",
                    "branch",
                    "remote",
                    "worktree",
                    "config",
                }
            ),
            prohibited_tokens=_GIT_DESTRUCTIVE,
            env_passthrough=GIT_ENV,
            timeout_seconds=60,
            description="Inspection of repository state.",
        ),
        ExecutionProfile(
            name="git-worktree",
            executables=frozenset({"git"}),
            allowed_subcommands=frozenset({"worktree", "branch", "rev-parse"}),
            # `git worktree remove --force` is required for retention cleanup
            # after the diff artifact is captured; other destructive flags stay
            # refused.
            prohibited_tokens=frozenset({"--hard", "--mirror", "--no-verify"}),
            env_passthrough=GIT_ENV,
            timeout_seconds=120,
            writes_allowed=True,
            description="Task branch and worktree lifecycle.",
        ),
        ExecutionProfile(
            name="git-commit",
            executables=frozenset({"git"}),
            allowed_subcommands=frozenset({"add", "commit", "status", "diff", "rev-parse"}),
            prohibited_tokens=_GIT_DESTRUCTIVE,
            env_passthrough=GIT_ENV,
            timeout_seconds=120,
            writes_allowed=True,
            description="Committing validated task output on a task branch.",
        ),
        ExecutionProfile(
            name="git-push",
            executables=frozenset({"git"}),
            allowed_subcommands=frozenset({"push"}),
            prohibited_tokens=_GIT_DESTRUCTIVE,
            env_passthrough=GIT_ENV,
            timeout_seconds=300,
            writes_allowed=True,
            network="required (git remote)",
            description="Safe branch push. Force variants are refused.",
        ),
        ExecutionProfile(
            name="github-readonly",
            executables=frozenset({"gh"}),
            allowed_verb_pairs=frozenset(
                {
                    ("pr", "view"),
                    ("pr", "list"),
                    ("pr", "diff"),
                    ("pr", "checks"),
                    ("issue", "view"),
                    ("issue", "list"),
                    ("label", "list"),
                    ("run", "list"),
                    ("run", "view"),
                    ("auth", "status"),
                    ("repo", "view"),
                }
            ),
            prohibited_tokens=frozenset({"-x", "--method", "-f", "-F", "--field", "--input"}),
            timeout_seconds=120,
            network="required (GitHub API)",
            description="Reading GitHub state.",
        ),
        ExecutionProfile(
            name="github-write-safe",
            executables=frozenset({"gh"}),
            allowed_verb_pairs=frozenset(
                {
                    ("pr", "create"),
                    ("pr", "comment"),
                    ("pr", "edit"),
                    ("issue", "create"),
                    ("issue", "comment"),
                    ("issue", "edit"),
                    ("label", "create"),
                    ("api", "repos"),
                }
            ),
            # merge/approve/ready/delete are absent from the verb pairs by design
            prohibited_tokens=frozenset(
                {"--admin", "--approve", "--delete-branch", "--merge", "--squash", "--rebase"}
            ),
            timeout_seconds=120,
            writes_allowed=True,
            network="required (GitHub API)",
            description="Issues, comments, labels, and draft PRs. Never merges.",
        ),
        ExecutionProfile(
            name="validation-trusted",
            executables=frozenset(
                {
                    "uv",
                    "npm",
                    "npx",
                    "node",
                    "python3",
                    "pytest",
                    "ruff",
                    "mypy",
                    "make",
                    "tsc",
                    "alembic",
                    "go",
                    "cargo",
                }
            ),
            env_passthrough=NODE_ENV,
            timeout_seconds=900,
            max_output_bytes=1_000_000,
            writes_allowed=True,
            network="package installs may require network",
            description="Validation commands for trusted repositories only.",
        ),
        ExecutionProfile(
            name="validation-untrusted",
            executables=frozenset(),  # nothing may run; approval gate required
            approval_action="run-untrusted-repository-scripts",
            description="Untrusted repositories: host execution refused; "
            "requires containerization or an explicit approval gate.",
        ),
        ExecutionProfile(
            name="migration-local",
            executables=frozenset({"uv", "alembic"}),
            allowed_subcommands=frozenset(
                {"run", "upgrade", "downgrade", "history", "current", "revision"}
            ),
            timeout_seconds=300,
            writes_allowed=True,
            description="Nexus's own database migrations.",
        ),
        ExecutionProfile(
            name="tool-install",
            executables=frozenset({"npm"}),
            allowed_subcommands=frozenset({"install", "view", "config", "ls"}),
            prohibited_tokens=frozenset({"--unsafe-perm"}),
            env_passthrough=NODE_ENV,
            timeout_seconds=600,
            writes_allowed=True,
            network="required (npm registry)",
            description="Installing official worker CLIs (e.g. @openai/codex).",
        ),
    ]
}


def get_profile(name: str) -> ExecutionProfile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ProfileViolation(f"unknown execution profile: {name}") from None
