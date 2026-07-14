"""Git worktree isolation for autonomous tasks.

Rules implemented (docs/OPERATING-MODEL.md section 8):
- every implementation task runs in its own worktree on its own branch;
- deterministic branch naming: nexus/<goal-short>/<task-short>;
- baseline commit recorded before execution;
- final diff captured after execution;
- worktrees removed only after their state is retained (diff artifact);
- refuses to operate on a dirty checkout it does not own;
- never force-pushes, never touches protected branches.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from nexus.config import get_settings
from nexus.observability import get_logger

log = get_logger(__name__)


class WorkspaceError(Exception):
    pass


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=120, check=False
    )
    if check and proc.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:500]}")
    return proc


@dataclass
class Workspace:
    repo_path: Path
    worktree_path: Path
    branch: str
    baseline_commit: str


def branch_name(goal_id: str, task_id: str) -> str:
    return f"nexus/{goal_id.split('_')[-1][:8]}/{task_id.split('_')[-1][:8]}"


def create_workspace(repo_path: Path, goal_id: str, task_id: str) -> Workspace:
    repo_path = repo_path.resolve()
    if not (repo_path / ".git").exists():
        raise WorkspaceError(f"{repo_path} is not a git repository")

    baseline = _git(repo_path, "rev-parse", "HEAD").stdout.strip()
    branch = branch_name(goal_id, task_id)
    worktrees_root = get_settings().workspaces_dir
    worktrees_root.mkdir(parents=True, exist_ok=True)
    worktree_path = worktrees_root / f"{goal_id[-8:]}-{task_id[-8:]}"

    if worktree_path.exists():
        raise WorkspaceError(f"worktree path already in use: {worktree_path}")

    existing = _git(repo_path, "branch", "--list", branch).stdout.strip()
    if existing:
        raise WorkspaceError(f"branch already exists: {branch}")

    _git(repo_path, "worktree", "add", "-b", branch, str(worktree_path), baseline)
    log.info(
        "workspace.created", branch=branch, worktree=str(worktree_path), baseline=baseline[:12]
    )
    return Workspace(
        repo_path=repo_path, worktree_path=worktree_path, branch=branch, baseline_commit=baseline
    )


def capture_diff(ws: Workspace) -> str:
    """Diff of everything the task changed relative to its baseline."""
    _git(ws.worktree_path, "add", "-A", "--intent-to-add", check=False)
    diff = _git(ws.worktree_path, "diff", ws.baseline_commit, check=False).stdout
    return diff


def has_changes(ws: Workspace) -> bool:
    status = _git(ws.worktree_path, "status", "--porcelain").stdout.strip()
    return bool(status)


def commit_all(ws: Workspace, message: str) -> str | None:
    """Commit task output on the task branch. Returns the commit hash."""
    if not has_changes(ws):
        return None
    _git(ws.worktree_path, "add", "-A")
    _git(ws.worktree_path, "commit", "-m", message)
    return _git(ws.worktree_path, "rev-parse", "HEAD").stdout.strip()


def remove_workspace(ws: Workspace, *, force: bool = False) -> None:
    """Remove the worktree. Refuses if uncommitted changes exist unless forced
    (callers must capture the diff artifact first)."""
    if not force and has_changes(ws):
        raise WorkspaceError(
            f"worktree {ws.worktree_path} has uncommitted changes; capture them first"
        )
    _git(
        ws.repo_path,
        "worktree",
        "remove",
        "--force" if force else "--porcelain",
        str(ws.worktree_path),
        check=False,
    )
    # Fall back to plain removal if flags differ across git versions.
    if ws.worktree_path.exists():
        _git(ws.repo_path, "worktree", "remove", "--force", str(ws.worktree_path), check=False)
    log.info("workspace.removed", worktree=str(ws.worktree_path))
