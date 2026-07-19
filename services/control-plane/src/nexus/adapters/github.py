"""GitHub adapter.

Uses the officially authenticated `gh` CLI (no token handling in Nexus, no
scraping). Every call goes through a runner function so tests can inject a
fake; production routes through the centralized execution subsystem with the
github-readonly / github-write-safe / git-push profiles (ADR-006), which
refuse merges, approvals, and force pushes at the profile layer.
"""

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nexus.config import get_settings
from nexus.observability import get_logger

log = get_logger(__name__)

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]

_READONLY_VERBS = {
    ("pr", "view"),
    ("pr", "list"),
    ("pr", "diff"),
    ("pr", "checks"),
    ("issue", "view"),
    ("issue", "list"),
    ("label", "list"),
    ("run", "list"),
    ("run", "view"),
    ("repo", "view"),
}


def _profile_for(argv: list[str]) -> str:
    executable = Path(argv[0]).name
    if executable == "git":
        return "git-push"
    positional = [token for token in argv[1:] if not token.startswith("-")]
    pair = tuple(positional[:2])
    if pair == ("auth", "status"):
        return "worker-auth-status"
    return "github-readonly" if pair in _READONLY_VERBS else "github-write-safe"


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Production runner: centralized execution with profile enforcement."""
    from nexus.execution import get_profile, get_runner

    settings = get_settings()
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    cwd = settings.cache_dir
    roots = [settings.cache_dir]
    if Path(argv[0]).name == "git" and len(argv) >= 3 and argv[1] == "-C":
        # `git -C <worktree> push ...`: run inside the worktree so the git-push
        # profile sees `push` as the subcommand and the cwd is confined.
        cwd = Path(argv[2])
        roots = [cwd, settings.workspaces_dir]
        argv = [argv[0], *argv[3:]]
    result = get_runner().run(get_profile(_profile_for(argv)), argv, cwd=cwd, permitted_roots=roots)
    return subprocess.CompletedProcess(
        argv,
        result.exit_code if result.exit_code is not None else 1,
        result.stdout,
        result.stderr or (result.error or ""),
    )


class GitHubError(Exception):
    pass


@dataclass
class IssueRef:
    number: int
    url: str


@dataclass
class PullRequestRef:
    number: int
    url: str


NEXUS_LABELS: dict[str, str] = {
    "type:goal": "1D76DB",
    "type:feature": "0E8A16",
    "type:bug": "D93F0B",
    "type:architecture": "5319E7",
    "type:security": "B60205",
    "type:documentation": "0075CA",
    "status:ready": "C2E0C6",
    "status:running": "FBCA04",
    "status:blocked": "D93F0B",
    "status:review": "BFD4F2",
    "status:done": "0E8A16",
    "worker:claude": "8250DF",
    "worker:codex": "1F883D",
    "worker:auto": "6E7781",
    "risk:low": "DDF4FF",
    "risk:medium": "FFF8C5",
    "risk:high": "FFEBE9",
    "approval:required": "B60205",
}


class GitHubAdapter:
    def __init__(self, repo: str | None = None, runner: Runner | None = None) -> None:
        settings = get_settings()
        self.repo = repo or settings.github_repo
        self.gh = settings.gh_bin
        self._run = runner or _default_runner

    def _gh(self, *args: str, expect_json: bool = False):
        argv = [self.gh, *args]
        if self.repo and args[0] in {"issue", "pr", "label"}:
            argv += ["--repo", self.repo]
        proc = self._run(argv)
        if proc.returncode != 0:
            raise GitHubError(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()[:500]}")
        if expect_json:
            try:
                return json.loads(proc.stdout or "null")
            except ValueError as exc:
                raise GitHubError(f"gh returned non-JSON output: {proc.stdout[:200]}") from exc
        return proc.stdout

    def auth_status(self) -> dict[str, object]:
        proc = self._run([self.gh, "auth", "status"])
        return {
            "authenticated": proc.returncode == 0,
            "detail": (proc.stdout + proc.stderr).strip()[:500],
        }

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> IssueRef:
        args = ["issue", "create", "--title", title, "--body", body]
        for label in labels or []:
            args += ["--label", label]
        url = str(self._gh(*args)).strip()
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        log.info("github.issue-created", number=number)
        return IssueRef(number=number, url=url)

    def comment_issue(self, number: int, body: str) -> None:
        self._gh("issue", "comment", str(number), "--body", body)

    def create_pull_request(
        self, *, title: str, body: str, head: str, base: str = "main", draft: bool = True
    ) -> PullRequestRef:
        args = ["pr", "create", "--title", title, "--body", body, "--head", head, "--base", base]
        if draft:
            args.append("--draft")
        url = str(self._gh(*args)).strip().splitlines()[-1]
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        log.info("github.pr-created", number=number, head=head, draft=draft)
        return PullRequestRef(number=number, url=url)

    def post_validation_summary(self, pr_number: int, summary: str) -> None:
        self._gh("pr", "comment", str(pr_number), "--body", summary)

    def get_pr_review_comments(self, pr_number: int) -> list[dict[str, object]]:
        data = self._gh(
            "pr", "view", str(pr_number), "--json", "reviews,comments", expect_json=True
        )
        comments: list[dict[str, object]] = []
        for item in (data or {}).get("comments", []):
            comments.append(
                {"author": item.get("author", {}).get("login"), "body": item.get("body", "")}
            )
        for item in (data or {}).get("reviews", []):
            if item.get("body"):
                comments.append(
                    {"author": item.get("author", {}).get("login"), "body": item.get("body", "")}
                )
        return comments

    def ensure_labels(self) -> list[str]:
        """Idempotently create the Nexus label set. Returns labels created."""
        existing_raw = self._gh(
            "label", "list", "--json", "name", "--limit", "200", expect_json=True
        )
        existing = {item["name"] for item in existing_raw or []}
        created = []
        for name, color in NEXUS_LABELS.items():
            if name in existing:
                continue
            self._gh("label", "create", name, "--color", color)
            created.append(name)
        return created

    def push_branch(self, worktree_path: str, branch: str) -> None:
        """Push a task branch. Never force-pushes."""
        proc = self._run(["git", "-C", worktree_path, "push", "-u", "origin", branch])
        if proc.returncode != 0:
            raise GitHubError(f"git push failed: {proc.stderr.strip()[:500]}")
