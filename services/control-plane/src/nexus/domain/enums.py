"""Canonical status and classification enums for the Nexus domain."""

from enum import StrEnum


class GoalStatus(StrEnum):
    DRAFT = "draft"
    PLANNING = "planning"
    READY = "ready"
    EXECUTING = "executing"
    BLOCKED = "blocked"
    REVIEW = "review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    PENDING = "pending"  # created, dependencies not yet satisfied
    READY = "ready"  # dependencies satisfied, not yet queued
    QUEUED = "queued"  # claimable by the orchestrator
    RUNNING = "running"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    REVIEW = "review"
    BLOCKED = "blocked"  # waiting on an approval gate
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class Autonomy(StrEnum):
    MANUAL = "manual"  # every task requires approval before execution
    BOUNDED = "bounded"  # tasks run automatically within policy limits


class WorkerName(StrEnum):
    CLAUDE_CODE = "claude-code"
    CODEX_CLI = "codex-cli"
    FAKE = "fake"


class TaskKind(StrEnum):
    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    REVIEW = "review"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    REFACTORING = "refactoring"
    SECURITY_REVIEW = "security-review"
    ARCHITECTURE = "architecture"
    MAINTENANCE = "maintenance"


class ValidationKind(StrEnum):
    TESTS = "tests"
    INTEGRATION_TESTS = "integration-tests"
    LINT = "lint"
    FORMAT = "format"
    TYPECHECK = "typecheck"
    BUILD = "build"
    SECURITY = "security"
    SECRET_SCAN = "secret-scan"  # noqa: S105 (validation kind, not a password)
    DEPENDENCY_AUDIT = "dependency-audit"
    MIGRATION_CHECK = "migration-check"
    FILES_EXIST = "files-exist"
    CHANGED_SCOPE = "changed-scope"
    ACCEPTANCE = "acceptance"


class ValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"  # explicitly not requested; NEVER equivalent to passed
    BLOCKED = "blocked"  # requested but cannot run (trust, missing command/tool)
    ERROR = "error"  # infrastructure failure while running the check


class TrustLevel(StrEnum):
    UNTRUSTED = "untrusted"
    REVIEWED = "reviewed"
    TRUSTED_LOCAL = "trusted-local"
    TRUSTED_OWNER_APPROVED = "trusted-owner-approved"


# Trust levels whose repository-defined scripts may execute on the host.
HOST_EXECUTION_TRUST = {TrustLevel.TRUSTED_LOCAL, TrustLevel.TRUSTED_OWNER_APPROVED}


class FailureCategory(StrEnum):
    WORKER_CRASH = "worker-crash"
    AUTH = "auth"
    RATE_LIMIT = "rate-limit"
    TIMEOUT = "timeout"
    INVALID_OUTPUT = "invalid-output"
    POLICY_VIOLATION = "policy-violation"
    VALIDATION_FAILED = "validation-failed"
    REVIEW_REJECTED = "review-rejected"
    MERGE_CONFLICT = "merge-conflict"
    GIT_FAILURE = "git-failure"
    TOOL_UNAVAILABLE = "tool-unavailable"
    INFRASTRUCTURE = "infrastructure"
    OWNER_DENIED = "owner-denied"
    SIMULATED = "simulated"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


# Categories where a retry can plausibly change the outcome. Deterministic
# failures (policy, config, owner denial) never consume model usage on retry.
RETRYABLE_FAILURES = {
    FailureCategory.WORKER_CRASH,
    FailureCategory.TIMEOUT,
    FailureCategory.INVALID_OUTPUT,
    FailureCategory.VALIDATION_FAILED,
    FailureCategory.REVIEW_REJECTED,
    FailureCategory.MERGE_CONFLICT,
    FailureCategory.SIMULATED,
    FailureCategory.UNKNOWN,
}


class ReviewVerdict(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes-requested"
    BLOCKED = "blocked"
