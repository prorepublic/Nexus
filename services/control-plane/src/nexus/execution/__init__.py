"""Centralized execution subsystem (ADR-006).

Every subprocess Nexus launches flows through ProcessRunner with a
purpose-specific ExecutionProfile. There are no parallel execution paths.
"""

from nexus.execution.paths import PathEscapeError, confine_path
from nexus.execution.profiles import (
    PROFILES,
    ExecutionProfile,
    ProfileViolation,
    get_profile,
)
from nexus.execution.runner import ExecutionResult, ProcessRunner, get_runner

__all__ = [
    "PROFILES",
    "ExecutionProfile",
    "ExecutionResult",
    "PathEscapeError",
    "ProcessRunner",
    "ProfileViolation",
    "confine_path",
    "get_profile",
    "get_runner",
]
