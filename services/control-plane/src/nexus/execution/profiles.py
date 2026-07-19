"""Purpose-specific execution profiles with operation-level command schemas
(ADR-006, hardened in the V1 acceptance pass).

Each profile narrowly defines what one class of Nexus-initiated subprocess may
do. Enforcement is operation-level, not first-token-level:

- every invocation must match one explicit CommandRule (positional prefix,
  flag allowlist, value-taking flags), so a read-only profile rejects every
  mutating variant regardless of flag order, `--flag=value` form, or aliases;
- prohibited tokens are refused in ANY position as a second layer;
- flags outside a rule's allowlist are refused (unknown flags fail closed);
- profiles that handle sensitive input (worker prompts) never receive the
  prompt in argv at all — it travels via stdin (see runner.run stdin_data)
  and only a hash/size is ever logged.

Network policy is documented per profile but not kernel-enforced on macOS
without extra tooling; see docs/THREAT-MODEL.md for the honest residual risk.
"""

from dataclasses import dataclass
from pathlib import Path

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

_MAX_TOKEN_IN_ERROR = 60


class ProfileViolation(Exception):
    pass


def _short(token: str) -> str:
    """Tokens in error messages are truncated so an exception can never
    reproduce a full prompt or file body."""
    return token if len(token) <= _MAX_TOKEN_IN_ERROR else token[:_MAX_TOKEN_IN_ERROR] + "..."


@dataclass(frozen=True)
class CommandRule:
    """One permitted operation shape.

    subcommand: required positional prefix, in order.
    flags: allowed flag basenames (before any '='). Empty = no flags allowed.
    value_flags: flags that consume the following token as a value.
    allow_extra_positionals: whether positionals beyond the prefix are allowed.
    allow_any_flags: read-only inspection commands (git diff/log/show) where
        flag enumeration is impractical; mutating flags must then be covered
        by the profile's prohibited_tokens.
    """

    subcommand: tuple[str, ...] = ()
    flags: frozenset[str] = frozenset()
    value_flags: frozenset[str] = frozenset()
    required_flags: frozenset[str] = frozenset()  # must be present to match
    allow_extra_positionals: bool = True
    allow_any_flags: bool = False


@dataclass(frozen=True)
class ParsedArgv:
    positionals: tuple[str, ...]
    flag_bases: tuple[str, ...]
    flag_values: tuple[tuple[str, str], ...]  # (flag, value) for value-taking flags


def parse_argv_tail(tokens: list[str], value_flags: frozenset[str]) -> ParsedArgv:
    positionals: list[str] = []
    flag_bases: list[str] = []
    flag_values: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-") and token != "-":  # noqa: S105 (CLI token, not a password)
            base = token.split("=", 1)[0]
            flag_bases.append(base)
            if "=" in token:
                flag_values.append((base, token.split("=", 1)[1]))
            elif base in value_flags and index + 1 < len(tokens):
                index += 1
                flag_values.append((base, tokens[index]))
        else:
            positionals.append(token)
        index += 1
    return ParsedArgv(tuple(positionals), tuple(flag_bases), tuple(flag_values))


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    executables: frozenset[str]
    # Explicit permitted operations. None => only executables/prohibited checks
    # apply (used solely for validation-trusted, whose commands are separately
    # schema-checked at repository-profile time).
    rules: tuple[CommandRule, ...] | None = None
    # Tokens refused anywhere in argv, regardless of order or position.
    prohibited_tokens: frozenset[str] = frozenset()
    env_passthrough: tuple[str, ...] = DEFAULT_ENV
    timeout_seconds: int = 300
    max_output_bytes: int = 512_000
    network: str = "none-expected"  # documented intent, not kernel-enforced
    approval_action: str | None = None  # approval kind required before running
    # Path confinement (enforced by the runner when permitted_roots are given):
    # values of these flags must resolve inside the permitted roots.
    path_value_flags: frozenset[str] = frozenset()
    # When true: any token containing a '..' path component is refused, and
    # absolute-path positionals must resolve inside the permitted roots.
    confine_path_args: bool = False
    description: str = ""

    @property
    def all_value_flags(self) -> frozenset[str]:
        merged: set[str] = set(self.path_value_flags)
        for rule in self.rules or ():
            merged |= rule.value_flags
        return frozenset(merged)

    def check(self, argv: list[str]) -> ParsedArgv:
        if not argv or any(not isinstance(token, str) for token in argv):
            raise ProfileViolation(f"[{self.name}] argv must be a non-empty list of strings")
        executable = Path(argv[0]).name
        if executable not in self.executables:
            raise ProfileViolation(f"[{self.name}] executable not permitted: {_short(executable)}")

        lowered = [token.lower() for token in argv[1:]]
        for token in lowered:
            base = token.split("=", 1)[0]
            if token in self.prohibited_tokens or base in self.prohibited_tokens:
                raise ProfileViolation(f"[{self.name}] prohibited token: {_short(base)}")

        parsed = parse_argv_tail(argv[1:], self.all_value_flags)
        if self.rules is None:
            return parsed

        failures: list[str] = []
        for rule in self.rules:
            problem = self._match_rule(rule, parsed)
            if problem is None:
                return parsed
            failures.append(problem)
        operation = " ".join(_short(p) for p in parsed.positionals[:3]) or "(none)"
        raise ProfileViolation(
            f"[{self.name}] operation not permitted: {operation} "
            f"(no rule matched; e.g. {failures[0] if failures else 'no rules defined'})"
        )

    def _match_rule(self, rule: CommandRule, parsed: ParsedArgv) -> str | None:
        prefix = parsed.positionals[: len(rule.subcommand)]
        if prefix != rule.subcommand:
            return f"expected subcommand {' '.join(rule.subcommand) or '(none)'}"
        if not rule.allow_extra_positionals and len(parsed.positionals) > len(rule.subcommand):
            extra = parsed.positionals[len(rule.subcommand)]
            return f"unexpected positional {_short(extra)}"
        if not rule.allow_any_flags:
            for base in parsed.flag_bases:
                if base not in rule.flags:
                    return f"flag not allowed: {_short(base)}"
        for required in rule.required_flags:
            if required not in parsed.flag_bases:
                return f"missing required flag {required}"
        return None


def sanitize_argv(profile: "ExecutionProfile", argv: list[str]) -> list[str]:
    """A log/audit-safe summary of a command: executable, up to two positional
    subcommand tokens, and flag basenames. Flag VALUES and further positionals
    (which may contain prompts, file bodies, or repository content) are never
    included — only a count."""
    if not argv:
        return []
    parsed = parse_argv_tail(argv[1:], profile.all_value_flags)
    safe: list[str] = [Path(argv[0]).name]
    safe.extend(token[:40] for token in parsed.positionals[:2])
    safe.extend(sorted({base[:40] for base in parsed.flag_bases})[:8])
    hidden = max(0, len(argv) - 1 - (len(safe) - 1))
    if hidden:
        safe.append(f"(+{hidden} args)")
    return safe


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
        "-D",
        "--amend",
        "--no-verify",
        "-c",  # inline config override
        "-C",  # repository root override defeats cwd confinement
        "--git-dir",
        "--work-tree",
        "--exec-path",
    }
)

_VERSION_RULE = CommandRule(
    flags=frozenset({"--version", "-v", "-V"}), allow_extra_positionals=False
)

PROFILES: dict[str, ExecutionProfile] = {
    profile.name: profile
    for profile in [
        # -- health and authentication inspection (never mutation) -----------
        ExecutionProfile(
            name="worker-health-readonly",
            executables=frozenset(
                {
                    "claude",
                    "codex",
                    "git",
                    "gh",
                    "docker",
                    "node",
                    "npm",
                    "python3",
                    "uv",
                    "psql",
                    "security",
                }
            ),
            rules=(
                _VERSION_RULE,
                CommandRule(
                    subcommand=("find-generic-password",),
                    flags=frozenset({"-s"}),
                    value_flags=frozenset({"-s"}),
                    allow_extra_positionals=False,
                ),
            ),
            timeout_seconds=30,
            max_output_bytes=64_000,
            description="Version checks and keychain presence probes. No writes, no auth mutation.",
        ),
        ExecutionProfile(
            name="worker-auth-status",
            executables=frozenset({"gh", "codex"}),
            rules=(
                CommandRule(subcommand=("auth", "status"), allow_extra_positionals=False),
                CommandRule(subcommand=("login", "status"), allow_extra_positionals=False),
            ),
            timeout_seconds=30,
            max_output_bytes=64_000,
            network="may contact the provider to validate a token",
            description="Authentication STATUS only. Login/logout are owner-run "
            "interactive commands and are not executable through any profile.",
        ),
        # -- model workers (prompts travel via stdin, never argv) -------------
        ExecutionProfile(
            name="worker-claude",
            executables=frozenset({"claude"}),
            rules=(
                CommandRule(
                    flags=frozenset(
                        {
                            "-p",
                            "--output-format",
                            "--verbose",
                            "--max-turns",
                            "--allowedTools",
                            "--add-dir",
                            "--no-session-persistence",
                            "--json-schema",
                        }
                    ),
                    value_flags=frozenset(
                        {
                            "--output-format",
                            "--max-turns",
                            "--allowedTools",
                            "--add-dir",
                            "--json-schema",
                        }
                    ),
                    allow_extra_positionals=False,
                ),
            ),
            prohibited_tokens=frozenset(
                {
                    "--dangerously-skip-permissions",
                    "--allow-dangerously-skip-permissions",
                    "--resume",
                    "--continue",
                    "-c",
                }
            ),
            env_passthrough=NODE_ENV,
            timeout_seconds=1800,
            max_output_bytes=2_000_000,
            path_value_flags=frozenset({"--add-dir"}),
            confine_path_args=True,
            network="required (Anthropic API via subscription)",
            description="Non-interactive Claude Code runs inside a task workspace. "
            "The task instruction is delivered via stdin.",
        ),
        ExecutionProfile(
            name="worker-codex",
            executables=frozenset({"codex"}),
            rules=(
                CommandRule(
                    subcommand=("exec",),
                    flags=frozenset({"--json", "--sandbox", "--cd", "--skip-git-repo-check"}),
                    value_flags=frozenset({"--sandbox", "--cd"}),
                    allow_extra_positionals=True,  # the "-" stdin marker
                ),
            ),
            prohibited_tokens=frozenset(
                {"danger-full-access", "--dangerously-bypass-approvals-and-sandbox", "--yolo"}
            ),
            env_passthrough=NODE_ENV,
            timeout_seconds=1800,
            max_output_bytes=2_000_000,
            path_value_flags=frozenset({"--cd"}),
            confine_path_args=True,
            network="required (OpenAI API via subscription)",
            description="Non-interactive Codex runs inside a task workspace. "
            "The task instruction is delivered via stdin ('-').",
        ),
        # -- git ---------------------------------------------------------------
        ExecutionProfile(
            name="git-clone",
            executables=frozenset({"git"}),
            rules=(
                CommandRule(subcommand=("clone",)),
                CommandRule(subcommand=("fetch",)),
            ),
            prohibited_tokens=_GIT_DESTRUCTIVE,
            env_passthrough=GIT_ENV,
            timeout_seconds=600,
            confine_path_args=True,
            network="required (git remote)",
            description="Cloning a repository during onboarding.",
        ),
        ExecutionProfile(
            name="git-readonly",
            executables=frozenset({"git"}),
            rules=(
                CommandRule(subcommand=("status",), flags=frozenset({"--porcelain"})),
                CommandRule(subcommand=("diff",), allow_any_flags=True),
                CommandRule(subcommand=("log",), allow_any_flags=True),
                CommandRule(subcommand=("show",), allow_any_flags=True),
                CommandRule(subcommand=("ls-files",), allow_any_flags=True),
                CommandRule(
                    subcommand=("rev-parse",),
                    flags=frozenset({"--abbrev-ref", "--is-inside-work-tree"}),
                ),
                CommandRule(
                    subcommand=("branch",),
                    flags=frozenset({"--list"}),
                    required_flags=frozenset({"--list"}),
                ),
                CommandRule(subcommand=("remote", "get-url")),
                CommandRule(subcommand=("worktree", "list")),
            ),
            prohibited_tokens=_GIT_DESTRUCTIVE,
            env_passthrough=GIT_ENV,
            timeout_seconds=60,
            confine_path_args=True,
            description="Inspection of repository state. git config is not "
            "readable or writable through any Nexus profile.",
        ),
        ExecutionProfile(
            name="git-worktree",
            executables=frozenset({"git"}),
            rules=(
                CommandRule(
                    subcommand=("worktree", "add"),
                    flags=frozenset({"-b"}),
                    value_flags=frozenset({"-b"}),
                ),
                # remove --force is required for retention cleanup after the
                # diff artifact is captured.
                CommandRule(subcommand=("worktree", "remove"), flags=frozenset({"--force"})),
                CommandRule(subcommand=("worktree", "list")),
                CommandRule(subcommand=("worktree", "prune")),
                CommandRule(subcommand=("rev-parse",), flags=frozenset({"--abbrev-ref"})),
                CommandRule(
                    subcommand=("branch",),
                    flags=frozenset({"--list"}),
                    required_flags=frozenset({"--list"}),
                ),
            ),
            prohibited_tokens=frozenset(
                {"--hard", "--mirror", "--no-verify", "-C", "-c", "--git-dir", "--work-tree", "-f"}
            ),
            env_passthrough=GIT_ENV,
            timeout_seconds=120,
            confine_path_args=True,
            description="Task branch and worktree lifecycle.",
        ),
        ExecutionProfile(
            name="git-commit",
            executables=frozenset({"git"}),
            rules=(
                CommandRule(subcommand=("add",), flags=frozenset({"-A", "--intent-to-add"})),
                CommandRule(
                    subcommand=("commit",), flags=frozenset({"-m"}), value_flags=frozenset({"-m"})
                ),
                CommandRule(subcommand=("status",), flags=frozenset({"--porcelain"})),
                CommandRule(subcommand=("diff",), allow_any_flags=True),
                CommandRule(subcommand=("rev-parse",), flags=frozenset({"--abbrev-ref"})),
            ),
            prohibited_tokens=_GIT_DESTRUCTIVE,
            env_passthrough=GIT_ENV,
            timeout_seconds=120,
            confine_path_args=True,
            description="Committing validated task output on a task branch.",
        ),
        ExecutionProfile(
            name="git-push",
            executables=frozenset({"git"}),
            rules=(CommandRule(subcommand=("push",), flags=frozenset({"-u"})),),
            prohibited_tokens=_GIT_DESTRUCTIVE,
            env_passthrough=GIT_ENV,
            timeout_seconds=300,
            confine_path_args=True,
            network="required (git remote)",
            description="Safe branch push. Force variants are refused.",
        ),
        # -- gh ----------------------------------------------------------------
        ExecutionProfile(
            name="github-readonly",
            executables=frozenset({"gh"}),
            rules=(
                CommandRule(
                    subcommand=("pr", "view"),
                    flags=frozenset({"--json", "--jq", "--repo", "--limit"}),
                    value_flags=frozenset({"--json", "--jq", "--repo", "--limit"}),
                ),
                CommandRule(
                    subcommand=("pr", "list"),
                    flags=frozenset({"--json", "--jq", "--repo", "--limit", "--state"}),
                    value_flags=frozenset({"--json", "--jq", "--repo", "--limit", "--state"}),
                ),
                CommandRule(
                    subcommand=("pr", "diff"),
                    flags=frozenset({"--repo"}),
                    value_flags=frozenset({"--repo"}),
                ),
                CommandRule(
                    subcommand=("pr", "checks"),
                    flags=frozenset({"--repo", "--json"}),
                    value_flags=frozenset({"--repo", "--json"}),
                ),
                CommandRule(
                    subcommand=("issue", "view"),
                    flags=frozenset({"--json", "--jq", "--repo"}),
                    value_flags=frozenset({"--json", "--jq", "--repo"}),
                ),
                CommandRule(
                    subcommand=("issue", "list"),
                    flags=frozenset({"--json", "--jq", "--repo", "--limit"}),
                    value_flags=frozenset({"--json", "--jq", "--repo", "--limit"}),
                ),
                CommandRule(
                    subcommand=("label", "list"),
                    flags=frozenset({"--json", "--repo", "--limit"}),
                    value_flags=frozenset({"--json", "--repo", "--limit"}),
                ),
                CommandRule(
                    subcommand=("run", "list"),
                    flags=frozenset({"--json", "--repo", "--limit"}),
                    value_flags=frozenset({"--json", "--repo", "--limit"}),
                ),
                CommandRule(
                    subcommand=("run", "view"),
                    flags=frozenset({"--json", "--repo"}),
                    value_flags=frozenset({"--json", "--repo"}),
                ),
                CommandRule(
                    subcommand=("repo", "view"),
                    flags=frozenset({"--json", "--jq"}),
                    value_flags=frozenset({"--json", "--jq"}),
                ),
            ),
            prohibited_tokens=frozenset(
                {"-x", "--method", "-f", "-F", "--field", "--input", "--web"}
            ),
            timeout_seconds=120,
            network="required (GitHub API)",
            description="Reading GitHub state.",
        ),
        ExecutionProfile(
            name="github-write-safe",
            executables=frozenset({"gh"}),
            rules=(
                CommandRule(
                    subcommand=("issue", "create"),
                    flags=frozenset({"--title", "--body", "--label", "--repo"}),
                    value_flags=frozenset({"--title", "--body", "--label", "--repo"}),
                ),
                CommandRule(
                    subcommand=("issue", "comment"),
                    flags=frozenset({"--body", "--repo"}),
                    value_flags=frozenset({"--body", "--repo"}),
                ),
                CommandRule(
                    subcommand=("issue", "edit"),
                    flags=frozenset({"--title", "--body", "--repo", "--add-label"}),
                    value_flags=frozenset({"--title", "--body", "--repo", "--add-label"}),
                ),
                CommandRule(
                    subcommand=("pr", "create"),
                    flags=frozenset({"--title", "--body", "--head", "--base", "--draft", "--repo"}),
                    value_flags=frozenset({"--title", "--body", "--head", "--base", "--repo"}),
                ),
                CommandRule(
                    subcommand=("pr", "comment"),
                    flags=frozenset({"--body", "--repo"}),
                    value_flags=frozenset({"--body", "--repo"}),
                ),
                CommandRule(
                    subcommand=("pr", "edit"),
                    flags=frozenset({"--title", "--body", "--repo", "--add-label"}),
                    value_flags=frozenset({"--title", "--body", "--repo", "--add-label"}),
                ),
                CommandRule(
                    subcommand=("label", "create"),
                    flags=frozenset({"--color", "--force", "--repo"}),
                    value_flags=frozenset({"--color", "--repo"}),
                ),
            ),
            # merge / approve / ready / close / delete have no matching rule and
            # are additionally token-prohibited as a second layer.
            prohibited_tokens=frozenset(
                {
                    "--admin",
                    "--approve",
                    "--delete-branch",
                    "--merge",
                    "--squash",
                    "--rebase",
                    "--web",
                }
            ),
            timeout_seconds=120,
            network="required (GitHub API)",
            description="Issues, comments, labels, and draft PRs. Never merges, "
            "approves, or marks ready for review.",
        ),
        # -- validation ---------------------------------------------------------
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
            rules=None,  # commands are schema-checked when the owner sets the
            # repository validation profile (validate_profile_config)
            env_passthrough=NODE_ENV,
            timeout_seconds=900,
            max_output_bytes=1_000_000,
            confine_path_args=True,
            network="package installs may require network",
            description="Owner-configured validation commands for trusted repositories only.",
        ),
        ExecutionProfile(
            name="validation-untrusted",
            executables=frozenset(),  # nothing may run; approval gate required
            approval_action="run-untrusted-repository-scripts",
            description="Untrusted repositories: host execution refused; "
            "requires an explicit approval gate.",
        ),
        # -- nexus self-management ----------------------------------------------
        ExecutionProfile(
            name="migration-local",
            executables=frozenset({"uv", "alembic"}),
            rules=(
                CommandRule(subcommand=("run", "alembic")),
                CommandRule(subcommand=("upgrade",)),
                CommandRule(subcommand=("downgrade",)),
                CommandRule(subcommand=("history",)),
                CommandRule(subcommand=("current",)),
                CommandRule(
                    subcommand=("revision",),
                    flags=frozenset({"--autogenerate", "-m"}),
                    value_flags=frozenset({"-m"}),
                ),
            ),
            timeout_seconds=300,
            confine_path_args=True,
            description="Nexus's own database migrations.",
        ),
        ExecutionProfile(
            name="service-local",
            executables=frozenset({"launchctl"}),
            rules=(
                CommandRule(subcommand=("load",), flags=frozenset({"-w"})),
                CommandRule(subcommand=("unload",), flags=frozenset({"-w"})),
                CommandRule(subcommand=("list",)),
            ),
            timeout_seconds=30,
            description="Managing Nexus's own launchd agent.",
        ),
        ExecutionProfile(
            name="tool-install",
            executables=frozenset({"npm"}),
            rules=(
                CommandRule(subcommand=("install",), flags=frozenset({"-g"})),
                CommandRule(subcommand=("view",)),
                CommandRule(subcommand=("ls",), flags=frozenset({"-g"})),
            ),
            prohibited_tokens=frozenset({"--unsafe-perm", "config"}),
            env_passthrough=NODE_ENV,
            timeout_seconds=600,
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
