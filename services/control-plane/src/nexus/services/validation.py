"""Evidence-based completion: a task is done when validation says so,
not when a worker claims so."""

from dataclasses import dataclass
from pathlib import Path

from nexus.domain.enums import ValidationKind
from nexus.policies.command import CommandExecutor, CommandPolicy

# Validation command templates per kind. Only run when the workspace opts in
# (marker file present), because arbitrary repos have arbitrary toolchains.
_COMMANDS: dict[ValidationKind, tuple[list[str], str]] = {
    ValidationKind.TESTS: (["make", "test"], "Makefile"),
    ValidationKind.LINT: (["make", "lint"], "Makefile"),
    ValidationKind.TYPECHECK: (["make", "typecheck"], "Makefile"),
    ValidationKind.BUILD: (["make", "build"], "Makefile"),
}


@dataclass
class ValidationOutcome:
    kind: ValidationKind
    passed: bool
    summary: str


def validate_workspace(
    workspace: Path,
    kinds: list[ValidationKind],
    expected_files: list[str] | None = None,
    executor: CommandExecutor | None = None,
) -> list[ValidationOutcome]:
    outcomes: list[ValidationOutcome] = []
    executor = executor or CommandExecutor(CommandPolicy(workspace=workspace))

    for kind in kinds:
        if kind == ValidationKind.FILES_EXIST:
            missing = [rel for rel in (expected_files or []) if not (workspace / rel).exists()]
            outcomes.append(
                ValidationOutcome(
                    kind=kind,
                    passed=not missing,
                    summary="all expected files exist"
                    if not missing
                    else f"missing: {', '.join(missing[:10])}",
                )
            )
            continue

        entry = _COMMANDS.get(kind)
        if entry is None:
            outcomes.append(
                ValidationOutcome(
                    kind=kind, passed=True, summary="no automated check defined; skipped"
                )
            )
            continue
        argv, marker = entry
        if not (workspace / marker).exists():
            outcomes.append(
                ValidationOutcome(kind=kind, passed=True, summary=f"skipped ({marker} not present)")
            )
            continue
        result = executor.run(argv, cwd=workspace)
        tail = (result.stdout + result.stderr)[-800:]
        outcomes.append(
            ValidationOutcome(
                kind=kind, passed=result.ok, summary=("passed" if result.ok else f"failed: {tail}")
            )
        )
    return outcomes
