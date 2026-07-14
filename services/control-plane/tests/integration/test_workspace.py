"""Git worktree isolation. Uses throwaway local repositories; marked
integration because it shells out to git (no network, no PostgreSQL)."""

import subprocess
from pathlib import Path

import pytest

from nexus.services import workspace as ws

pytestmark = pytest.mark.integration


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, check=True, capture_output=True)
    return repo_path


class TestWorkspaceIsolation:
    def test_create_records_baseline_and_branch(self, repo):
        workspace = ws.create_workspace(repo, "goal_abc12345", "task_def67890")
        assert workspace.worktree_path.exists()
        assert workspace.branch.startswith("nexus/")
        assert len(workspace.baseline_commit) == 40

    def test_two_tasks_get_distinct_worktrees(self, repo):
        first = ws.create_workspace(repo, "goal_aaaa1111", "task_bbbb2222")
        second = ws.create_workspace(repo, "goal_aaaa1111", "task_cccc3333")
        assert first.worktree_path != second.worktree_path
        assert first.branch != second.branch

    def test_same_task_cannot_double_create(self, repo):
        ws.create_workspace(repo, "goal_aaaa1111", "task_bbbb2222")
        with pytest.raises(ws.WorkspaceError):
            ws.create_workspace(repo, "goal_aaaa1111", "task_bbbb2222")

    def test_changes_isolated_from_main_checkout(self, repo):
        workspace = ws.create_workspace(repo, "goal_a1", "task_b2")
        (workspace.worktree_path / "new-file.py").write_text("x = 1\n")
        assert not (repo / "new-file.py").exists()
        assert ws.has_changes(workspace)

    def test_diff_captured_and_commit(self, repo):
        workspace = ws.create_workspace(repo, "goal_a1", "task_b2")
        (workspace.worktree_path / "feature.py").write_text("y = 2\n")
        diff = ws.capture_diff(workspace)
        assert "feature.py" in diff
        commit = ws.commit_all(workspace, "task work")
        assert commit is not None
        assert not ws.has_changes(workspace)

    def test_removal_refused_with_uncommitted_changes(self, repo):
        workspace = ws.create_workspace(repo, "goal_a1", "task_b2")
        (workspace.worktree_path / "wip.txt").write_text("unsaved\n")
        with pytest.raises(ws.WorkspaceError, match="uncommitted"):
            ws.remove_workspace(workspace)
        ws.remove_workspace(workspace, force=True)
        assert not workspace.worktree_path.exists()

    def test_non_repo_refused(self, tmp_path):
        with pytest.raises(ws.WorkspaceError, match="not a git repository"):
            ws.create_workspace(tmp_path, "goal_x", "task_y")
