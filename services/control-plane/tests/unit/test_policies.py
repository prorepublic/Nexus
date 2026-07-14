from pathlib import Path

import pytest

from nexus.domain.enums import Autonomy, Risk
from nexus.policies.approval import approval_required
from nexus.policies.command import (
    CommandExecutor,
    CommandPolicy,
    CommandPolicyError,
)
from nexus.policies.cost import DEFAULT_COST_POLICY, WorkerBudgetState


class TestApprovalPolicy:
    @pytest.mark.parametrize(
        "action",
        [
            "merge-to-main",
            "production-deploy",
            "enable-paid-service",
            "force-push",
            "danger-full-access",
            "purchase-domain",
            "change-billing",
            "disable-security-validation",
            "delete-database",
        ],
    )
    def test_dangerous_actions_always_gated(self, action):
        assert approval_required(action).required
        # Even at low risk with bounded autonomy:
        assert approval_required(action, Risk.LOW, Autonomy.BOUNDED).required

    @pytest.mark.parametrize(
        "action",
        [
            "create-branch",
            "create-worktree",
            "run-tests",
            "run-linters",
            "create-documentation",
            "create-github-issue",
            "open-draft-pr",
            "bounded-repair",
            "safe-dev-migration",
        ],
    )
    def test_routine_actions_never_interrupt(self, action):
        assert not approval_required(action).required

    def test_manual_autonomy_gates_code_writes(self):
        decision = approval_required("write-code-in-workspace", autonomy=Autonomy.MANUAL)
        assert decision.required

    def test_bounded_autonomy_allows_code_writes(self):
        assert not approval_required("write-code-in-workspace", autonomy=Autonomy.BOUNDED).required

    def test_unknown_high_risk_fails_safe(self):
        assert approval_required("mystery-action", Risk.HIGH).required

    def test_unknown_low_risk_bounded_proceeds(self):
        assert not approval_required("mystery-action", Risk.LOW).required


class TestCommandPolicy:
    def test_allowlisted_command_runs(self, tmp_path: Path):
        executor = CommandExecutor(CommandPolicy(workspace=tmp_path))
        result = executor.run(["echo", "hello"], cwd=tmp_path)
        assert result.ok
        assert "hello" in result.stdout

    def test_unlisted_executable_refused(self, tmp_path: Path):
        executor = CommandExecutor(CommandPolicy(workspace=tmp_path))
        with pytest.raises(CommandPolicyError, match="not allowlisted"):
            executor.run(["curl", "http://example.com"], cwd=tmp_path)

    @pytest.mark.parametrize(
        "argv",
        [
            ["git", "push", "--force"],
            ["git", "push", "origin", "main", "--force"],
            ["git", "push", "-f"],
            ["git", "reset", "--hard"],
            ["rm", "-rf", "/"],
        ],
    )
    def test_destructive_commands_refused(self, argv, tmp_path: Path):
        executor = CommandExecutor(CommandPolicy(workspace=tmp_path))
        with pytest.raises(CommandPolicyError, match="destructive|not allowlisted"):
            executor.run(argv, cwd=tmp_path)

    def test_normal_git_push_is_not_flagged(self, tmp_path: Path):
        policy = CommandPolicy(workspace=tmp_path)
        policy.check(["git", "push", "origin", "feature-branch"], cwd=tmp_path)

    def test_cwd_escape_refused(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        executor = CommandExecutor(CommandPolicy(workspace=workspace))
        with pytest.raises(CommandPolicyError, match="escapes"):
            executor.run(["echo", "x"], cwd=tmp_path)

    def test_timeout_enforced(self, tmp_path: Path):
        executor = CommandExecutor(
            CommandPolicy(
                workspace=tmp_path, timeout_seconds=1, allowed_executables=frozenset({"python3"})
            )
        )
        result = executor.run(["python3", "-c", "import time; time.sleep(5)"], cwd=tmp_path)
        assert result.timed_out
        assert not result.ok

    def test_output_capped(self, tmp_path: Path):
        executor = CommandExecutor(
            CommandPolicy(
                workspace=tmp_path, max_output_bytes=100, allowed_executables=frozenset({"python3"})
            )
        )
        result = executor.run(["python3", "-c", "print('x' * 10000)"], cwd=tmp_path)
        assert result.truncated
        assert len(result.stdout) < 200

    def test_secret_redaction_in_output(self, tmp_path: Path):
        executor = CommandExecutor(
            CommandPolicy(workspace=tmp_path, allowed_executables=frozenset({"python3"}))
        )
        result = executor.run(
            ["python3", "-c", "print('token: ghp_" + "a" * 36 + "')"], cwd=tmp_path
        )
        assert "ghp_" not in result.stdout
        assert "[REDACTED]" in result.stdout


class TestCostPolicy:
    def test_everything_paid_is_disabled(self):
        described = DEFAULT_COST_POLICY.describe()
        assert described["paid_apis_enabled"] is False
        assert all(value is False for value in described["flags"].values())

    def test_budget_state_marks_and_clears(self):
        state = WorkerBudgetState()
        assert state.is_available("claude-code")
        state.mark_exhausted("claude-code", "5h limit reached")
        assert not state.is_available("claude-code")
        state.clear("claude-code")
        assert state.is_available("claude-code")
