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
    LINT = "lint"
    TYPECHECK = "typecheck"
    BUILD = "build"
    SECURITY = "security"
    FILES_EXIST = "files-exist"
    ACCEPTANCE = "acceptance"
