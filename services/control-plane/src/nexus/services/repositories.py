"""Repository registration and onboarding (ADR-007).

A repository-backed goal may only execute once its repository has been
registered and onboarding checks pass. Registration collects the metadata the
orchestrator, validation engine, and GitHub adapter need; trust starts at
'untrusted' and must be raised deliberately by the owner.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexus.config import get_settings
from nexus.db.models import Repository, utcnow
from nexus.domain.enums import TrustLevel
from nexus.execution import get_profile, get_runner
from nexus.observability import get_logger
from nexus.services.validation import InvalidValidationProfile, validate_profile_config

log = get_logger(__name__)


class RepositoryError(Exception):
    pass


_GITHUB_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _git_ro(repo: Path, *args: str):
    return get_runner().run(
        get_profile("git-readonly"), ["git", *args], cwd=repo, permitted_roots=[repo]
    )


# Language/toolchain detection markers -> (language, package_manager,
# suggested validation profile entries). Suggestions only: they still require
# raised trust before they execute on the host.
_MARKERS: list[tuple[str, str, str, dict[str, dict[str, list[str]]]]] = [
    (
        "pyproject.toml",
        "python",
        "uv",
        {
            "lint": {"command": ["uv", "run", "ruff", "check", "."]},
            "typecheck": {"command": ["uv", "run", "mypy", "."]},
            "tests": {"command": ["uv", "run", "pytest", "-q"]},
        },
    ),
    (
        "package.json",
        "typescript",
        "npm",
        {
            "lint": {"command": ["npm", "run", "lint"]},
            "build": {"command": ["npm", "run", "build"]},
            "tests": {"command": ["npm", "test"]},
        },
    ),
    (
        "go.mod",
        "go",
        "go",
        {
            "build": {"command": ["go", "build", "./..."]},
            "tests": {"command": ["go", "test", "./..."]},
        },
    ),
    (
        "Cargo.toml",
        "rust",
        "cargo",
        {"build": {"command": ["cargo", "build"]}, "tests": {"command": ["cargo", "test"]}},
    ),
]


@dataclass
class RepoInspection:
    local_path: Path
    default_branch: str
    current_commit: str
    remote_url: str | None
    github_slug: str | None
    languages: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    suggested_validation: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    clean: bool = True


def resolve_source(source: str) -> Path:
    """Resolve a registration source (local path | owner/repo | GitHub URL) to a
    local checkout, cloning into ~/.nexus/repos when needed."""
    candidate = Path(source).expanduser()
    if candidate.exists():
        if not (candidate / ".git").exists():
            raise RepositoryError(f"{candidate} exists but is not a git repository")
        return candidate.resolve()

    slug: str | None = None
    if _GITHUB_SLUG.match(source):
        slug = source
    elif source.startswith("https://github.com/"):
        slug = source.removeprefix("https://github.com/").removesuffix(".git").rstrip("/")
    if slug is None:
        raise RepositoryError(
            f"cannot resolve repository source: {source!r} "
            "(expected a local path, owner/repo, or GitHub URL)"
        )

    repos_root = get_settings().workspaces_dir.parent / "repos"
    repos_root.mkdir(parents=True, exist_ok=True)
    target = repos_root / slug.split("/")[-1]
    if target.exists():
        return target.resolve()
    result = get_runner().run(
        get_profile("git-clone"),
        ["git", "clone", f"https://github.com/{slug}.git", str(target)],
        cwd=repos_root,
        permitted_roots=[repos_root],
    )
    if not result.ok:
        raise RepositoryError(f"clone failed: {result.stderr.strip()[:400]}")
    return target.resolve()


def inspect_repository(local_path: Path) -> RepoInspection:
    local_path = local_path.resolve()
    if not (local_path / ".git").exists():
        raise RepositoryError(f"{local_path} is not a git repository")

    commit = _git_ro(local_path, "rev-parse", "HEAD")
    if not commit.ok:
        raise RepositoryError(f"cannot read HEAD: {commit.stderr.strip()[:200]}")

    branch_result = _git_ro(local_path, "rev-parse", "--abbrev-ref", "HEAD")
    default_branch = branch_result.stdout.strip() or "main"
    origin_head = _git_ro(local_path, "rev-parse", "--abbrev-ref", "origin/HEAD")
    if origin_head.ok and "/" in origin_head.stdout:
        default_branch = origin_head.stdout.strip().split("/", 1)[1]

    remote = _git_ro(local_path, "remote", "get-url", "origin")
    remote_url = remote.stdout.strip() if remote.ok and remote.stdout.strip() else None
    github_slug = None
    if remote_url:
        match = re.search(r"github\.com[:/]([^/]+/[^/.]+)", remote_url)
        if match:
            github_slug = match.group(1)

    languages: list[str] = []
    package_managers: list[str] = []
    suggested: dict[str, dict[str, list[str]]] = {}
    for marker, language, manager, profile in _MARKERS:
        if (local_path / marker).exists() or list(local_path.glob(f"*/{marker}"))[:1]:
            languages.append(language)
            package_managers.append(manager)
            if not suggested:
                suggested = profile

    status = _git_ro(local_path, "status", "--porcelain")
    return RepoInspection(
        local_path=local_path,
        default_branch=default_branch,
        current_commit=commit.stdout.strip(),
        remote_url=remote_url,
        github_slug=github_slug,
        languages=languages,
        package_managers=package_managers,
        suggested_validation=suggested,
        clean=not status.stdout.strip(),
    )


def register_repository(
    session: Session,
    source: str,
    *,
    name: str | None = None,
    apply_suggested_validation: bool = True,
) -> Repository:
    local_path = resolve_source(source)
    inspection = inspect_repository(local_path)
    repo_name = name or inspection.github_slug or local_path.name

    repo = session.scalars(select(Repository).where(Repository.name == repo_name)).first()
    if repo is None:
        repo = Repository(name=repo_name)
        session.add(repo)

    repo.local_path = str(local_path)
    repo.remote_url = inspection.remote_url
    repo.github_slug = inspection.github_slug
    repo.default_branch = inspection.default_branch
    repo.languages = inspection.languages
    repo.package_managers = inspection.package_managers
    repo.last_inspected_at = utcnow()
    repo.meta = {
        "current_commit": inspection.current_commit,
        "clean_at_registration": inspection.clean,
    }
    if apply_suggested_validation and inspection.suggested_validation:
        validate_profile_config(inspection.suggested_validation)
        repo.validation_profile = inspection.suggested_validation
    repo.onboarded = bool(doctor_repository(repo, session=None, quick=True) == [])
    session.flush()
    log.info(
        "repository.registered",
        name=repo_name,
        path=str(local_path),
        trust=repo.trust_level,
        onboarded=repo.onboarded,
    )
    return repo


def set_trust(session: Session, name: str, level: str) -> Repository:
    try:
        trust = TrustLevel(level)
    except ValueError:
        raise RepositoryError(
            f"invalid trust level {level!r}; valid: {', '.join(TrustLevel)}"
        ) from None
    repo = session.scalars(select(Repository).where(Repository.name == name)).first()
    if repo is None:
        raise RepositoryError(f"repository not registered: {name}")
    repo.trust_level = trust
    return repo


def set_validation_profile(session: Session, name: str, config: dict) -> Repository:
    repo = session.scalars(select(Repository).where(Repository.name == name)).first()
    if repo is None:
        raise RepositoryError(f"repository not registered: {name}")
    validate_profile_config(config)  # raises InvalidValidationProfile
    repo.validation_profile = config
    return repo


def doctor_repository(repo: Repository, session: Session | None, quick: bool = False) -> list[str]:
    """Onboarding checks. Returns a list of problems (empty = healthy)."""
    problems: list[str] = []
    if not repo.local_path:
        return ["no local path recorded"]
    path = Path(repo.local_path)
    if not path.exists():
        return [f"local path missing: {path}"]
    if not (path / ".git").exists():
        problems.append("not a git repository")
        return problems
    head = _git_ro(path, "rev-parse", "HEAD")
    if not head.ok:
        problems.append("cannot read HEAD (empty repository?)")
    if repo.validation_profile:
        try:
            validate_profile_config(repo.validation_profile)
        except InvalidValidationProfile as exc:
            problems.append(f"invalid validation profile: {exc}")
    if not quick:
        status = _git_ro(path, "status", "--porcelain")
        if status.stdout.strip():
            problems.append(
                f"{len(status.stdout.strip().splitlines())} uncommitted change(s) "
                "in the source checkout (task worktrees are unaffected)"
            )
    return problems


def get_repository(session: Session, name_or_id: str) -> Repository:
    repo = (
        session.get(Repository, name_or_id)
        or session.scalars(select(Repository).where(Repository.name == name_or_id)).first()
    )
    if repo is None:
        raise RepositoryError(f"repository not registered: {name_or_id}")
    return repo
