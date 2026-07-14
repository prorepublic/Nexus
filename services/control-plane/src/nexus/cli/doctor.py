"""`nexus doctor`: environment health report.

All checks execute through the health-readonly / git-readonly execution
profiles (ADR-006); doctor never launches subprocesses directly.
"""

import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from nexus.config import get_settings
from nexus.execution import get_profile, get_runner
from nexus.workers.registry import get_registry


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    warn: bool = False  # ok=False but non-blocking


def _health_run(argv: list[str], cwd: Path | None = None, profile: str = "health-readonly"):
    settings = get_settings()
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    return get_runner().run(get_profile(profile), argv, cwd=cwd or settings.cache_dir)


def _version_of(binary: str, *args: str) -> str | None:
    if shutil.which(binary) is None:
        return None
    result = _health_run([binary, *args])
    combined = (result.stdout or result.stderr).strip()
    if not combined:
        return "installed (version unknown)"
    return combined.splitlines()[0][:80]


def run_checks() -> list[Check]:
    settings = get_settings()
    checks: list[Check] = []

    checks.append(
        Check("os", True, f"{platform.system()} {platform.release()} ({platform.machine()})")
    )

    for name, binary, args in [
        ("git", "git", ["--version"]),
        ("github-cli", "gh", ["--version"]),
        ("docker", "docker", ["--version"]),
        ("node", "node", ["--version"]),
        ("npm", "npm", ["--version"]),
        ("python", "python3", ["--version"]),
        ("uv", "uv", ["--version"]),
    ]:
        version = _version_of(binary, *args)
        checks.append(Check(name, version is not None, version or "not found", warn=name in {"uv"}))

    if shutil.which("gh"):
        auth_result = _health_run(["gh", "auth", "status"])
        checks.append(
            Check(
                "github-auth",
                auth_result.ok,
                "authenticated" if auth_result.ok else "not authenticated (run `gh auth login`)",
            )
        )

    for name, health in get_registry().all_health().items():
        auth = {True: "authenticated", False: "NOT authenticated", None: "auth unknown"}[
            health.authenticated
        ]
        detail = (
            f"{health.version or 'not installed'}; {auth}; {health.detail}"
            if health.installed
            else health.detail
        )
        checks.append(Check(f"worker:{name}", health.installed, detail, warn=name != "fake"))

    try:
        from nexus.db.base import get_session_factory

        with get_session_factory()() as session:
            session.execute(text("SELECT 1"))
        checks.append(Check("postgresql", True, settings.database_url.split("@")[-1]))
    except Exception:
        checks.append(Check("postgresql", False, "unreachable — run `make db-up` (docker compose)"))

    checks.append(
        Check(
            "notion",
            settings.notion_token is not None,
            "configured"
            if settings.notion_token
            else "not configured (optional — run `nexus notion setup`)",
            warn=True,
        )
    )

    # Repository cleanliness of the current directory, when it is a git repo.
    cwd = Path.cwd()
    inside = get_runner().run(
        get_profile("git-readonly"), ["git", "rev-parse", "--is-inside-work-tree"], cwd=cwd
    )
    if inside.ok and inside.stdout.strip() == "true":
        dirty = (
            get_runner()
            .run(get_profile("git-readonly"), ["git", "status", "--porcelain"], cwd=cwd)
            .stdout.strip()
        )
        checks.append(
            Check(
                "repo-clean",
                not dirty,
                "working tree clean"
                if not dirty
                else f"{len(dirty.splitlines())} uncommitted change(s)",
                warn=True,
            )
        )
    return checks
