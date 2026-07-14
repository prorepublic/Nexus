"""Workspace path confinement.

Validates every path argument and model-generated path against an allowed
root. Rejects absolute paths outside the root, `..` traversal, and symlink
escape (the candidate is fully resolved before comparison, so a symlink
pointing outside the root fails even when its own path looks inside).
"""

from pathlib import Path


class PathEscapeError(Exception):
    def __init__(self, candidate: object, root: Path, reason: str) -> None:
        self.candidate = candidate
        self.root = root
        self.reason = reason
        super().__init__(f"path escape rejected ({reason}): {candidate!r} outside {root}")


def confine_path(candidate: str | Path, root: Path, *, allow_root_itself: bool = True) -> Path:
    """Return the resolved path if it stays inside root; raise otherwise."""
    raw = Path(candidate)
    if ".." in raw.parts:
        raise PathEscapeError(candidate, root, "parent traversal")
    root_resolved = root.resolve()
    joined = raw if raw.is_absolute() else root_resolved / raw
    # strict=False: the target may not exist yet (e.g. expected output files),
    # but any existing symlink components are still resolved.
    resolved = joined.resolve()
    if resolved == root_resolved:
        if allow_root_itself:
            return resolved
        raise PathEscapeError(candidate, root, "root itself not permitted")
    if root_resolved not in resolved.parents:
        raise PathEscapeError(candidate, root, "outside permitted root")
    return resolved


def confine_cwd(cwd: str | Path, roots: list[Path]) -> Path:
    """A working directory must live inside one of the permitted roots."""
    last_error: PathEscapeError | None = None
    for root in roots:
        try:
            return confine_path(cwd, root)
        except PathEscapeError as exc:
            last_error = exc
    if last_error is None:
        raise PathEscapeError(cwd, Path("/"), "no permitted roots configured")
    raise last_error
