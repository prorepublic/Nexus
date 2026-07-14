import json
import subprocess

import pytest

from nexus.adapters.github import NEXUS_LABELS, GitHubAdapter, GitHubError


class FakeGh:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.labels: set[str] = set()
        self.fail_next = False

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if self.fail_next:
            self.fail_next = False
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        joined = " ".join(argv)
        if "issue create" in joined:
            return subprocess.CompletedProcess(argv, 0, "https://github.com/o/r/issues/42\n", "")
        if "pr create" in joined:
            return subprocess.CompletedProcess(argv, 0, "https://github.com/o/r/pull/7\n", "")
        if "label list" in joined:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps([{"name": name} for name in self.labels]), ""
            )
        if "label create" in joined:
            self.labels.add(argv[argv.index("create") + 1])
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "auth status" in joined:
            return subprocess.CompletedProcess(argv, 0, "Logged in", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def gh():
    return FakeGh()


@pytest.fixture
def adapter(gh):
    return GitHubAdapter(repo="owner/repo", runner=gh)


class TestGitHubAdapter:
    def test_create_issue_parses_number(self, adapter):
        ref = adapter.create_issue("Title", "Body", labels=["type:goal"])
        assert ref.number == 42
        assert ref.url.endswith("/issues/42")

    def test_repo_flag_passed(self, adapter, gh):
        adapter.create_issue("T", "B")
        assert "--repo" in gh.calls[0] and "owner/repo" in gh.calls[0]

    def test_create_draft_pr(self, adapter, gh):
        ref = adapter.create_pull_request(title="T", body="B", head="nexus/x/y")
        assert ref.number == 7
        assert "--draft" in gh.calls[0]
        assert "--base" in gh.calls[0]

    def test_error_raises(self, adapter, gh):
        gh.fail_next = True
        with pytest.raises(GitHubError):
            adapter.create_issue("T", "B")

    def test_ensure_labels_idempotent(self, adapter, gh):
        created_first = adapter.ensure_labels()
        assert set(created_first) == set(NEXUS_LABELS)
        created_second = adapter.ensure_labels()
        assert created_second == []

    def test_auth_status(self, adapter):
        status = adapter.auth_status()
        assert status["authenticated"] is True

    def test_push_branch_never_forces(self, adapter, gh):
        adapter.push_branch("/tmp/wt", "nexus/a/b")
        push_call = gh.calls[-1]
        assert "--force" not in push_call and "-f" not in push_call
