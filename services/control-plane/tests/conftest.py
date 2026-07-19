import os
from collections.abc import Iterator
from pathlib import Path

import pytest

TEST_DB_URL = os.environ.get(
    "NEXUS_TEST_DATABASE_URL",
    "postgresql+psycopg://nexus:nexus_local_dev@127.0.0.1:5442/nexus_test",
)


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test gets isolated workspaces/cache dirs and a fresh settings cache."""
    from nexus.config import get_settings

    monkeypatch.setenv("NEXUS_WORKSPACES_DIR", str(tmp_path / "workspaces"))
    monkeypatch.setenv("NEXUS_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("NEXUS_DATABASE_URL", TEST_DB_URL)
    monkeypatch.setenv("NEXUS_PLANNER_MODE", "deterministic")
    monkeypatch.setenv("NEXUS_OWNER_TOKEN_FILE", str(tmp_path / "owner-token"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db_session():
    """Integration-test database: real PostgreSQL, fresh schema per test."""
    import psycopg
    from sqlalchemy.engine import make_url

    url = make_url(TEST_DB_URL)
    admin_dsn = (
        f"host={url.host} port={url.port} user={url.username} password={url.password} dbname=nexus"
    )
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (url.database,)
            ).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{url.database}"')
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not available (run `make db-up`)")

    from nexus.db import models  # noqa: F401
    from nexus.db.base import Base, get_engine, get_session_factory, reset_engine

    reset_engine()
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = get_session_factory()()
    yield session
    session.close()
    reset_engine()
