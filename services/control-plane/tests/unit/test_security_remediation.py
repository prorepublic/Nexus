"""Acceptance-pass security tests: prompt leakage, operation-level profile
enforcement, and path-argument confinement."""

from pathlib import Path

import pytest

from nexus.domain.enums import TaskKind
from nexus.execution import ProcessRunner, ProfileViolation, get_profile
from nexus.execution.profiles import ExecutionProfile, sanitize_argv
from nexus.workers.base import TaskSpec
from nexus.workers.claude_code import ClaudeCodeAdapter
from nexus.workers.codex_cli import CodexCliAdapter

SECRET = "ghp_" + "s" * 36  # matches the redaction patterns

STDIN_PROFILE = ExecutionProfile(
    name="test-stdin",
    executables=frozenset({"python3"}),
    rules=None,
    timeout_seconds=10,
    env_passthrough=("PATH", "HOME"),
)


class TestPromptLeakPrevention:
    def test_claude_argv_never_contains_instruction(self, tmp_path: Path):
        spec = TaskSpec(
            task_id="t",
            run_id="r",
            kind=TaskKind.IMPLEMENTATION,
            instruction=f"Implement the thing. Secret: {SECRET}",
            workspace=tmp_path,
        )
        argv = ClaudeCodeAdapter().build_argv(spec)
        assert all(SECRET not in token for token in argv)
        assert all(spec.instruction not in token for token in argv)

    def test_codex_argv_never_contains_instruction(self, tmp_path: Path):
        spec = TaskSpec(
            task_id="t",
            run_id="r",
            kind=TaskKind.IMPLEMENTATION,
            instruction=f"Implement the thing. Secret: {SECRET}",
            workspace=tmp_path,
        )
        argv = CodexCliAdapter().build_argv(spec)
        assert all(SECRET not in token for token in argv)
        assert "-" in argv  # stdin marker

    def test_stdin_reaches_child_but_never_audit_or_result_argv(self, tmp_path: Path):
        recorded: list[list[str]] = []

        def recorder(profile, safe_argv, cwd, exit_code, duration_ms, truncated, run_id):
            recorded.append(list(safe_argv))

        prompt = f"the task instruction with a secret {SECRET} inside"
        result = ProcessRunner(recorder=recorder).run(
            STDIN_PROFILE,
            ["python3", "-c", "import sys; data=sys.stdin.read(); print(len(data))"],
            cwd=tmp_path,
            stdin_data=prompt,
            run_id="run_x",
        )
        assert result.ok
        assert str(len(prompt)) in result.stdout  # the child really received it
        # sanitized argv in the result: no flag values, no prompt, no secret
        assert all(SECRET not in token for token in result.argv)
        assert all(prompt not in token for token in result.argv)
        # audit record: sanitized argv plus a hash reference only
        assert recorded, "audit recorder was not called"
        flat = " ".join(recorded[0])
        assert SECRET not in flat
        assert prompt not in flat
        assert "stdin:sha256:" in flat

    def test_child_echoing_secret_is_redacted_in_captured_output(self, tmp_path: Path):
        result = ProcessRunner().run(
            STDIN_PROFILE,
            ["python3", "-c", "import sys; print(sys.stdin.read())"],
            cwd=tmp_path,
            stdin_data=f"leak attempt {SECRET}",
        )
        assert SECRET not in result.stdout
        assert "[REDACTED]" in result.stdout

    def test_sanitize_argv_drops_flag_values_and_extra_positionals(self):
        profile = get_profile("worker-claude")
        argv = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--max-turns",
            "30",
            "--allowedTools",
            "Read Grep",
            "--add-dir",
            "/some/workspace",
        ]
        safe = sanitize_argv(profile, argv)
        joined = " ".join(safe)
        assert "stream-json" not in joined
        assert "/some/workspace" not in joined
        assert "Read Grep" not in joined
        assert "--add-dir" in joined  # flag NAMES are safe and useful

    def test_profile_violation_truncates_long_tokens(self):
        long_secret = "x" * 500 + SECRET
        with pytest.raises(ProfileViolation) as excinfo:
            get_profile("git-readonly").check(["git", long_secret])
        message = str(excinfo.value)
        assert SECRET not in message
        assert len(message) < 300


class TestOperationLevelEnforcement:
    @pytest.mark.parametrize(
        "profile_name,argv",
        [
            # git config is not allowed anywhere
            ("git-readonly", ["git", "config", "user.email", "evil@example.com"]),
            ("git-readonly", ["git", "config", "--global", "core.editor", "x"]),
            ("git-commit", ["git", "config", "user.email", "evil@example.com"]),
            # branch creation/deletion under read-only
            ("git-readonly", ["git", "branch", "new-branch"]),
            ("git-readonly", ["git", "branch", "-D", "victim"]),
            ("git-readonly", ["git", "branch", "--list", "-D", "victim"]),
            # worktree mutation under read-only
            ("git-readonly", ["git", "worktree", "add", "/tmp/x"]),
            ("git-readonly", ["git", "worktree", "remove", "x"]),
            # repository-root overrides defeat cwd confinement
            ("git-readonly", ["git", "-C", "/etc", "status"]),
            ("git-push", ["git", "-C", "/somewhere", "push", "origin", "main"]),
            ("git-readonly", ["git", "--git-dir", "/etc/repo", "log"]),
            # auth mutation is never allowed
            ("worker-health-readonly", ["gh", "auth", "login"]),
            ("worker-auth-status", ["gh", "auth", "login"]),
            ("worker-auth-status", ["codex", "login"]),
            ("worker-codex", ["codex", "login"]),
            ("worker-health-readonly", ["claude", "login"]),
            # npm config mutation
            ("tool-install", ["npm", "config", "set", "registry", "http://evil"]),
            # gh write escalation
            ("github-write-safe", ["gh", "pr", "merge", "1"]),
            ("github-write-safe", ["gh", "pr", "review", "--approve", "1"]),
            ("github-write-safe", ["gh", "pr", "ready", "1"]),
            ("github-readonly", ["gh", "api", "repos/o/r", "-X", "POST"]),
            # flag=value and reordered forms
            ("git-push", ["git", "push", "--force=true", "origin", "main"]),
            ("git-push", ["git", "push", "origin", "main", "--force-with-lease"]),
            ("git-commit", ["git", "commit", "--amend", "-m", "x"]),
            ("git-commit", ["git", "commit", "-m", "x", "--no-verify"]),
            # unknown flags fail closed on rule-based profiles
            ("git-readonly", ["git", "status", "--porcelain", "--mystery-flag"]),
            ("worker-claude", ["claude", "-p", "--settings", "/tmp/evil.json"]),
        ],
    )
    def test_mutating_or_unknown_variants_rejected(self, profile_name, argv):
        with pytest.raises(ProfileViolation):
            get_profile(profile_name).check(argv)

    @pytest.mark.parametrize(
        "profile_name,argv",
        [
            ("git-readonly", ["git", "status", "--porcelain"]),
            ("git-readonly", ["git", "branch", "--list", "nexus/x/y"]),
            ("git-readonly", ["git", "remote", "get-url", "origin"]),
            ("git-worktree", ["git", "worktree", "add", "-b", "nexus/a/b", "wt", "abc123"]),
            ("git-push", ["git", "push", "-u", "origin", "nexus/a/b"]),
            ("worker-health-readonly", ["codex", "--version"]),
            ("worker-auth-status", ["gh", "auth", "status"]),
            ("worker-auth-status", ["codex", "login", "status"]),
            (
                "github-write-safe",
                [
                    "gh",
                    "pr",
                    "create",
                    "--draft",
                    "--title",
                    "t",
                    "--body",
                    "b",
                    "--head",
                    "h",
                    "--base",
                    "main",
                ],
            ),
            ("tool-install", ["npm", "install", "-g", "@openai/codex"]),
        ],
    )
    def test_legitimate_operations_allowed(self, profile_name, argv):
        get_profile(profile_name).check(argv)

    def test_login_status_is_the_only_login_operation(self):
        """`codex login status` reads state; `codex login` mutates it."""
        profile = get_profile("worker-auth-status")
        profile.check(["codex", "login", "status"])
        with pytest.raises(ProfileViolation):
            profile.check(["codex", "login"])


class TestPathArgumentConfinement:
    def test_claude_add_dir_escape_rejected(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        argv = ["claude", "-p", "--add-dir", "/etc"]
        with pytest.raises(ProfileViolation, match="escapes permitted roots"):
            ProcessRunner().run(
                get_profile("worker-claude"),
                argv,
                cwd=ws,
                permitted_roots=[ws],
                stdin_data="prompt",
            )

    def test_codex_cd_escape_rejected(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        argv = ["codex", "exec", "--json", "--cd", str(tmp_path.parent), "-"]
        with pytest.raises(ProfileViolation, match="escapes permitted roots"):
            ProcessRunner().run(
                get_profile("worker-codex"),
                argv,
                cwd=ws,
                permitted_roots=[ws],
                stdin_data="prompt",
            )

    def test_safe_cwd_with_escaping_absolute_argument_rejected(self, tmp_path: Path):
        """cwd confinement alone is not enough: file arguments are checked too."""
        ws = tmp_path / "ws"
        ws.mkdir()
        with pytest.raises(ProfileViolation, match="escapes permitted roots"):
            ProcessRunner().run(
                get_profile("git-worktree"),
                ["git", "worktree", "add", "-b", "nexus/a/b", "/etc/nexus-evil", "HEAD"],
                cwd=ws,
                permitted_roots=[ws],
            )

    def test_parent_traversal_argument_rejected(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        with pytest.raises(ProfileViolation, match="parent traversal"):
            ProcessRunner().run(
                get_profile("git-readonly"),
                ["git", "diff", "../../outside.txt"],
                cwd=ws,
                permitted_roots=[ws],
            )

    def test_symlink_path_flag_escape_rejected(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = ws / "sneaky"
        link.symlink_to(outside)
        with pytest.raises(ProfileViolation, match="escapes permitted roots"):
            ProcessRunner().run(
                get_profile("worker-claude"),
                ["claude", "-p", "--add-dir", str(link)],
                cwd=ws,
                permitted_roots=[ws],
                stdin_data="prompt",
            )

    def test_inside_paths_accepted(self, tmp_path: Path):
        """Path checking must not break legitimate confined invocations."""
        ws = tmp_path / "ws"
        ws.mkdir()
        get_profile("worker-claude").check(["claude", "-p", "--add-dir", str(ws)])
        # full runner path check (claude binary may not exist; use check only)
        from nexus.execution.runner import _confine_path_arguments

        _confine_path_arguments(
            get_profile("worker-claude"),
            ["claude", "-p", "--add-dir", str(ws / "sub")],
            [ws],
        )
