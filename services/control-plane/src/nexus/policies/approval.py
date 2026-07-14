"""Risk-aware approval gates (docs/AUTONOMY-AND-APPROVALS.md).

Actions listed in OWNER_APPROVAL_REQUIRED always require an explicit owner
decision. Routine development actions never interrupt the owner.
"""

from dataclasses import dataclass

from nexus.domain.enums import Autonomy, Risk

# Action kinds that always require owner approval before execution.
OWNER_APPROVAL_REQUIRED: frozenset[str] = frozenset(
    {
        "merge-to-main",
        "production-deploy",
        "enable-paid-service",
        "delete-repository",
        "delete-branch-with-unique-work",
        "delete-cloud-resource",
        "delete-database",
        "delete-notion-content",
        "destructive-migration",
        "change-auth-architecture",
        "publish-externally",
        "modify-dns",
        "purchase-domain",
        "access-unrelated-directory",
        "send-external-communication",
        "expose-service-publicly",
        "rotate-owner-credentials",
        "danger-full-access",
        "disable-security-validation",
        "force-push",
        "change-billing",
    }
)

# Routine actions that must never create an approval gate.
NO_APPROVAL_REQUIRED: frozenset[str] = frozenset(
    {
        "create-branch",
        "create-worktree",
        "write-code-in-workspace",
        "run-tests",
        "run-linters",
        "create-documentation",
        "create-github-issue",
        "open-draft-pr",
        "update-nexus-notion-pages",
        "safe-dev-migration",
        "bounded-repair",
    }
)


@dataclass(frozen=True)
class ApprovalDecision:
    required: bool
    reason: str


def approval_required(
    action_kind: str,
    risk: Risk = Risk.LOW,
    autonomy: Autonomy = Autonomy.BOUNDED,
) -> ApprovalDecision:
    """Decide whether an action needs an owner approval gate."""
    if action_kind in OWNER_APPROVAL_REQUIRED:
        return ApprovalDecision(True, f"'{action_kind}' always requires owner approval")
    if action_kind in NO_APPROVAL_REQUIRED:
        if autonomy == Autonomy.MANUAL and action_kind == "write-code-in-workspace":
            return ApprovalDecision(True, "goal autonomy is 'manual': each task needs approval")
        return ApprovalDecision(False, f"'{action_kind}' is a routine development action")
    # Unknown action kinds fail safe: high risk requires approval.
    if risk == Risk.HIGH:
        return ApprovalDecision(True, f"unlisted action '{action_kind}' with high risk")
    if autonomy == Autonomy.MANUAL:
        return ApprovalDecision(True, "goal autonomy is 'manual'")
    return ApprovalDecision(False, f"unlisted action '{action_kind}' with {risk} risk")
