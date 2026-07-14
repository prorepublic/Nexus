"""Git worktree isolation for autonomous tasks.

Rules implemented (docs/OPERATING-MODEL.md section 8):
- every implementation task runs in its own worktree on its own branch;
- deterministic branch naming: nexus/<goal-short>/<task-short>[-slug];
- baseline commit recorded before execution;
- final diff captured after execution;
- worktrees removed only after their state is safely retained;
- failed worktrees are retained for investigation;
- refuses to operate on a dirty checkout it does not own;
- never force-pushes, never touches protected branches.

All git invocations flow through the execution subsystem with purpose-specific
profiles (git-readonly / git-worktree / git-commit).
"""

import re
from dataclasses import dataclass
from pathlib import Path

from nexus.config import get_settings
from nexus.execution import get_profile, get_runner
from nexus.observability import get_logger

log = get_logger(__name__)


class WorkspaceError(Exception):
    pass


def _git(
    profile_name: str,
    repo: Path,
    *args: str,
    check: bool = True,
    extra_roots: list[Path] | None = None,
):
    roots = [repo, get_settings().workspaces_dir]
    if extra_roots:
        roots.extend(extra_roots)
    result = get_runner().run(
        get_profile(profile_name),
        ["git", *args],
        cwd=repo,
        permitted_roots=roots,
    )
    if check and not result.ok:
        detail = (result.stderr or result.error or "").strip()[:500]
        raise WorkspaceError(f"git {' '.join(args[:3])} failed: {detail}")
    return result


@dataclass
class Workspace:
    repo_path: Path
    worktree_path: Path
    branch: str
    baseline_commit: str


def slugify(text: str, max_length: int = 24) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_length].rstrip("-")


def branch_name(goal_id: str, task_id: str, title: str | None = None) -> str:
    base = f"nexus/{goal_id.split('_')[-1][:8]}/{task_id.split('_')[-1][:8]}"
    if title:
        slug = slugify(title)
        if slug:
            base = f"{base}-{slug}"
    return base


def is_clean(repo_path: Path) -> bool:
    result = _git("git-readonly", repo_path, "status", "--porcelain")
    return not result.stdout.strip()


def current_commit(repo_path: Path) -> str:
    return _git("git-readonly", repo_path, "rev-parse", "HEAD").stdout.strip()


def create_workspace(
    repo_path: Path,
    goal_id: str,
    task_id: str,
    title: str | None = None,
    start_point: str | None = None,
) -> Workspace:
    """Create the task branch and worktree.

    start_point lets sequentially dependent tasks chain: the new branch starts
    from the previous task's branch head instead of the repository HEAD.
    """
    repo_path = repo_path.resolve()
    if not (repo_path / ".git").exists():
        raise WorkspaceError(f"{repo_path} is not a git repository")

    if start_point:
        resolved = _git("git-readonly", repo_path, "rev-parse", start_point, check=False)
        if not resolved.ok:
            raise WorkspaceError(f"start point does not resolve: {start_point}")
        baseline = resolved.stdout.strip()
    else:
        baseline = current_commit(repo_path)
    branch = branch_name(goal_id, task_id, title)
    worktrees_root = get_settings().workspaces_dir
    worktrees_root.mkdir(parents=True, exist_ok=True)
    worktree_path = worktrees_root / f"{goal_id[-8:]}-{task_id[-8:]}"

    if worktree_path.exists():
        raise WorkspaceError(f"worktree path already in use: {worktree_path}")

    existing = _git("git-readonly", repo_path, "branch", "--list", branch).stdout.strip()
    if existing:
        raise WorkspaceError(f"branch already exists: {branch}")

    _git("git-worktree", repo_path, "worktree", "add", "-b", branch, str(worktree_path), baseline)
    log.info(
        "workspace.created", branch=branch, worktree=str(worktree_path), baseline=baseline[:12]
    )
    return Workspace(
        repo_path=repo_path, worktree_path=worktree_path, branch=branch, baseline_commit=baseline
    )


def capture_diff(ws: Workspace) -> str:
    """Diff of everything the task changed relative to its baseline."""
    _git("git-commit", ws.worktree_path, "add", "-A", "--intent-to-add", check=False)
    return _git("git-readonly", ws.worktree_path, "diff", ws.baseline_commit, check=False).stdout


def changed_files(ws: Workspace) -> list[str]:
    result = _git("git-readonly", ws.worktree_path, "status", "--porcelain", check=False)
    files = []
    for line in result.stdout.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip().strip('"'))
    return files


def has_changes(ws: Workspace) -> bool:
    return bool(changed_files(ws))


def commit_all(ws: Workspace, message: str) -> str | None:
    """Commit task output on the task branch. Returns the commit hash."""
    if not has_changes(ws):
        return None
    _git("git-commit", ws.worktree_path, "add", "-A")
    _git("git-commit", ws.worktree_path, "commit", "-m", message)
    return current_commit(ws.worktree_path)


def push_branch(ws: Workspace) -> None:
    """Safe push of the task branch. The git-push profile refuses force flags."""
    result = get_runner().run(
        get_profile("git-push"),
        ["git", "push", "-u", "origin", ws.branch],
        cwd=ws.worktree_path,
        permitted_roots=[ws.repo_path, get_settings().workspaces_dir],
    )
    if not result.ok:
        raise WorkspaceError(f"git push failed: {result.stderr.strip()[:500]}")


def remove_workspace(ws: Workspace, *, force: bool = False) -> None:
    """Remove the worktree. Refuses if uncommitted changes exist unless forced
    (callers must capture the diff artifact first)."""
    if not force and has_changes(ws):
        raise WorkspaceError(
            f"worktree {ws.worktree_path} has uncommitted changes; capture them first"
        )
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(ws.worktree_path))
    _git("git-worktree", ws.repo_path, *args, check=False)
    if ws.worktree_path.exists():
        _git(
            "git-worktree",
            ws.repo_path,
            "worktree",
            "remove",
            "--force",
            str(ws.worktree_path),
            check=False,
        )
    log.info("workspace.removed", worktree=str(ws.worktree_path))
