import pytest

from nexus.domain.enums import Autonomy, Risk
from nexus.policies.approval import approval_required
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
