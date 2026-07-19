"""Application settings.

All configuration is environment-driven. Secrets are never defaulted here and
never logged; see nexus.observability for redaction.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXUS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://nexus:nexus_local_dev@127.0.0.1:5442/nexus"
    api_host: str = "127.0.0.1"
    api_port: int = 8400
    log_level: str = "INFO"
    log_json: bool = False

    # Finite-autonomy defaults (see docs/AUTONOMY-AND-APPROVALS.md)
    max_repair_attempts: int = 2
    task_timeout_seconds: int = 1800
    command_timeout_seconds: int = 600
    max_output_bytes: int = 512_000

    # Review policy: required | preferred | disabled (ADR-009)
    # - required: implementation tasks cannot complete without an independent
    #   review from a different worker;
    # - preferred: review runs when a distinct healthy worker exists, otherwise
    #   the single-worker fallback is recorded and the task may proceed;
    # - disabled: no agent review (tests / explicit owner policy only).
    review_policy: str = "preferred"
    max_review_attempts: int = 2

    # Queue reliability (ADR-010)
    lease_seconds: int = 2700  # task_timeout + validation headroom
    heartbeat_seconds: int = 30
    worker_cooldown_seconds: int = 900  # rate-limited worker back-off

    # Live planning (ADR-008): auto = live when available, deterministic
    # otherwise; deterministic|live to force.
    planner_mode: str = "auto"
    planning_timeout_seconds: int = 600

    # Worker CLI binaries (overridable for tests)
    claude_bin: str = "claude"
    codex_bin: str = "codex"
    gh_bin: str = "gh"

    # Where isolated worktrees are created
    workspaces_dir: Path = Path.home() / ".nexus" / "workspaces"
    cache_dir: Path = Path.home() / ".nexus" / "cache"
    # Local-owner API credential (generated on first use, chmod 600)
    owner_token_file: Path = Path.home() / ".nexus" / "owner-token"

    # Notion (optional)
    notion_token: str | None = None
    notion_parent_page: str | None = None

    # GitHub (optional) "owner/repo" used by the GitHub adapter by default
    github_repo: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
