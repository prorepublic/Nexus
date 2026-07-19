"""Evidence-based, FAIL-CLOSED validation (ADR-007).

Rules:
- a requested check without a configured command is BLOCKED, not passed;
- a repository whose trust level does not permit host execution gets BLOCKED
  command checks (approval gate `run-untrusted-repository-scripts`);
- SKIPPED is a distinct result and never counts as passed;
- an implementation task in a registered repository cannot complete on
  `files-exist` evidence alone;
- expected-file paths are confined to the workspace (escape = ERROR);
- every outcome records command, profile, timing, exit code, and summary.

Validation commands come from the repository's validation profile stored in
Nexus (never implicitly from repository Makefiles), and run through the
validation-trusted execution profile.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus.domain.enums import (
    HOST_EXECUTION_TRUST,
    TaskKind,
    TrustLevel,
    ValidationKind,
    ValidationStatus,
)
from nexus.execution import PathEscapeError, confine_path, get_profile, get_runner
from nexus.execution.profiles import ProfileViolation
from nexus.observability import get_logger

log = get_logger(__name__)

# Checks that never execute repository-controlled code.
_INTRINSIC_CHECKS = {
    ValidationKind.FILES_EXIST,
    ValidationKind.CHANGED_SCOPE,
    ValidationKind.SECRET_SCAN,
}

_SECRET_HINTS = [
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ntn_",
    "sk-ant-",
    "sk-proj-",
    "-----BEGIN RSA PRIVATE KEY",
    "-----BEGIN OPENSSH PRIVATE KEY",
    "-----BEGIN EC PRIVATE KEY",
    "AKIA",
]


class InvalidValidationProfile(Exception):
    pass


def validate_profile_config(config: dict[str, Any]) -> dict[str, list[str]]:
    """Validate a repository validation profile before anything executes.

    Expected shape: {"lint": {"command": ["uv", "run", "ruff", "check", "."]}, ...}
    Every command is pre-checked against the validation-trusted execution
    profile so a malicious profile cannot smuggle in an unlisted executable.
    """
    commands: dict[str, list[str]] = {}
    trusted = get_profile("validation-trusted")
    for kind, entry in (config or {}).items():
        try:
            ValidationKind(kind)
        except ValueError:
            raise InvalidValidationProfile(f"unknown validation kind: {kind}") from None
        if not isinstance(entry, dict) or "command" not in entry:
            raise InvalidValidationProfile(f"{kind}: entry must be a dict with 'command'")
        command = entry["command"]
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(token, str) for token in command)
        ):
            raise InvalidValidationProfile(f"{kind}: command must be a non-empty string list")
        try:
            trusted.check(command)
        except ProfileViolation as exc:
            raise InvalidValidationProfile(f"{kind}: {exc}") from None
        commands[kind] = command
    return commands


@dataclass
class ValidationOutcome:
    kind: ValidationKind
    status: ValidationStatus
    summary: str
    command: list[str] = field(default_factory=list)
    profile: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    started_at: str = ""
    finished_at: str = ""
    output_tail: str = ""

    @property
    def passed(self) -> bool:
        return self.status == ValidationStatus.PASSED


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _files_exist(workspace: Path, expected_files: list[str]) -> ValidationOutcome:
    started = _now()
    missing: list[str] = []
    for rel in expected_files or []:
        try:
            target = confine_path(rel, workspace)
        except PathEscapeError as exc:
            return ValidationOutcome(
                kind=ValidationKind.FILES_EXIST,
                status=ValidationStatus.ERROR,
                summary=f"unsafe expected-file path: {exc.reason}: {rel}",
                started_at=started,
                finished_at=_now(),
            )
        if not target.exists():
            missing.append(rel)
    if not expected_files:
        return ValidationOutcome(
            kind=ValidationKind.FILES_EXIST,
            status=ValidationStatus.BLOCKED,
            summary="files-exist requested but no expected files were declared",
            started_at=started,
            finished_at=_now(),
        )
    return ValidationOutcome(
        kind=ValidationKind.FILES_EXIST,
        status=ValidationStatus.PASSED if not missing else ValidationStatus.FAILED,
        summary="all expected files exist"
        if not missing
        else f"missing: {', '.join(missing[:10])}",
        started_at=started,
        finished_at=_now(),
    )


def _changed_scope(
    workspace: Path, changed: list[str], allowed_globs: list[str]
) -> ValidationOutcome:
    """Detect changes outside the task's declared scope."""
    started = _now()
    if not allowed_globs:
        return ValidationOutcome(
            kind=ValidationKind.CHANGED_SCOPE,
            status=ValidationStatus.SKIPPED,
            summary="no scope restriction declared",
            started_at=started,
            finished_at=_now(),
        )
    from fnmatch import fnmatch

    out_of_scope = [
        path for path in changed if not any(fnmatch(path, glob) for glob in allowed_globs)
    ]
    return ValidationOutcome(
        kind=ValidationKind.CHANGED_SCOPE,
        status=ValidationStatus.PASSED if not out_of_scope else ValidationStatus.FAILED,
        summary="all changes in scope"
        if not out_of_scope
        else f"out-of-scope changes: {', '.join(out_of_scope[:10])}",
        started_at=started,
        finished_at=_now(),
    )


def _secret_scan(workspace: Path, changed: list[str]) -> ValidationOutcome:
    """Built-in lightweight scan of changed files for obvious secret material."""
    started = _now()
    findings: list[str] = []
    for rel in changed[:500]:
        try:
            target = confine_path(rel, workspace)
        except PathEscapeError:
            findings.append(f"{rel}: path escape")
            continue
        if not target.is_file() or target.stat().st_size > 1_000_000:
            continue
        try:
            content = target.read_text(errors="ignore")
        except OSError:
            continue
        for hint in _SECRET_HINTS:
            if hint in content:
                findings.append(f"{rel}: contains '{hint}...' pattern")
                break
    return ValidationOutcome(
        kind=ValidationKind.SECRET_SCAN,
        status=ValidationStatus.PASSED if not findings else ValidationStatus.FAILED,
        summary="no obvious secrets in changed files"
        if not findings
        else f"possible secrets: {'; '.join(findings[:5])}",
        started_at=started,
        finished_at=_now(),
    )


def validate_workspace(
    workspace: Path,
    kinds: list[ValidationKind],
    *,
    profile_commands: dict[str, list[str]] | None = None,
    trust_level: TrustLevel = TrustLevel.UNTRUSTED,
    expected_files: list[str] | None = None,
    changed: list[str] | None = None,
    allowed_globs: list[str] | None = None,
    run_id: str | None = None,
) -> list[ValidationOutcome]:
    outcomes: list[ValidationOutcome] = []
    commands = profile_commands or {}
    runner = get_runner()
    trusted_profile = get_profile("validation-trusted")

    for kind in kinds:
        if kind == ValidationKind.FILES_EXIST:
            outcomes.append(_files_exist(workspace, list(expected_files or [])))
            continue
        if kind == ValidationKind.CHANGED_SCOPE:
            outcomes.append(_changed_scope(workspace, changed or [], allowed_globs or []))
            continue
        if kind == ValidationKind.SECRET_SCAN:
            outcomes.append(_secret_scan(workspace, changed or []))
            continue

        command = commands.get(str(kind))
        started = _now()
        if command is None:
            outcomes.append(
                ValidationOutcome(
                    kind=kind,
                    status=ValidationStatus.BLOCKED,
                    summary=f"no validation command configured for '{kind}' "
                    "(set one with `nexus repo validation set`)",
                    started_at=started,
                    finished_at=_now(),
                )
            )
            continue
        if trust_level not in HOST_EXECUTION_TRUST:
            outcomes.append(
                ValidationOutcome(
                    kind=kind,
                    status=ValidationStatus.BLOCKED,
                    summary=f"repository trust level '{trust_level}' does not permit "
                    "host execution of repository scripts; raise trust with "
                    "`nexus repo trust` or approve 'run-untrusted-repository-scripts'",
                    command=command,
                    started_at=started,
                    finished_at=_now(),
                )
            )
            continue

        result = runner.run(
            trusted_profile,
            command,
            cwd=workspace,
            permitted_roots=[workspace],
            run_id=run_id,
        )
        tail = (result.stdout + result.stderr)[-1200:]
        if result.error:
            status = ValidationStatus.ERROR
            summary = f"could not execute: {result.error}"
        elif result.timed_out:
            status = ValidationStatus.ERROR
            summary = f"timed out after {trusted_profile.timeout_seconds}s"
        elif result.ok:
            status = ValidationStatus.PASSED
            summary = "passed"
        else:
            status = ValidationStatus.FAILED
            summary = f"failed (exit {result.exit_code})"
        outcomes.append(
            ValidationOutcome(
                kind=kind,
                status=status,
                summary=summary,
                command=command,
                profile=trusted_profile.name,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                started_at=started,
                finished_at=_now(),
                output_tail=tail,
            )
        )
    return outcomes


def evidence_sufficient(
    outcomes: list[ValidationOutcome],
    task_kind: TaskKind,
    repository_backed: bool,
) -> tuple[bool, str]:
    """Fail-closed completion rule.

    - every requested outcome must be PASSED (skipped/blocked/error never pass);
    - an empty outcome list never completes an implementation task;
    - files-exist alone is insufficient evidence for implementation tasks in a
      registered repository.
    """
    if not outcomes:
        if task_kind in {TaskKind.IMPLEMENTATION, TaskKind.REFACTORING, TaskKind.TESTING}:
            return False, "no validation was executed; implementation requires evidence"
        return True, "no validation required for this task kind"
    not_passed = [outcome for outcome in outcomes if not outcome.passed]
    if not_passed:
        detail = "; ".join(f"{o.kind}={o.status}" for o in not_passed[:6])
        return False, f"validation not satisfied: {detail}"
    if repository_backed and task_kind == TaskKind.IMPLEMENTATION:
        meaningful = {
            outcome.kind
            for outcome in outcomes
            if outcome.kind not in {ValidationKind.FILES_EXIST, ValidationKind.CHANGED_SCOPE}
        }
        if not meaningful:
            return False, (
                "files-exist alone is insufficient evidence for repository "
                "implementation tasks; configure command-backed validation"
            )
    return True, "all required validations passed"
