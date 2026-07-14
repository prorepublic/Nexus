"""`nexus doctor`: environment health report."""

import platform
import shutil
import subprocess
from dataclasses import dataclass

from sqlalchemy import text

from nexus.config import get_settings
from nexus.workers.registry import get_registry


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    warn: bool = False  # ok=False but non-blocking


def _version_of(binary: str, *args: str) -> str | None:
    path = shutil.which(binary)
    if path is None:
        return None
    try:
        proc = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=15, check=False
        )
        return (proc.stdout or proc.stderr).strip().splitlines()[0][:80]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "installed (version unknown)"


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

    gh = shutil.which("gh")
    if gh:
        proc = subprocess.run(
            [gh, "auth", "status"], capture_output=True, text=True, timeout=20, check=False
        )
        checks.append(
            Check(
                "github-auth",
                proc.returncode == 0,
                "authenticated"
                if proc.returncode == 0
                else "not authenticated (run `gh auth login`)",
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
    proc = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False
    )
    if proc.returncode == 0 and proc.stdout.strip() == "true":
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=False
        ).stdout.strip()
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
