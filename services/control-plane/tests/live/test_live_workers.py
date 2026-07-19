"""Explicitly invoked live-worker smoke tests.

Never run in CI or by `make test`. These consume real subscription usage:

    uv run pytest -m live tests/live -q
"""

from pathlib import Path

import pytest

from nexus.domain.enums import TaskKind
from nexus.workers.base import TaskSpec
from nexus.workers.claude_code import ClaudeCodeAdapter
from nexus.workers.codex_cli import CodexCliAdapter

pytestmark = pytest.mark.live


@pytest.mark.parametrize("adapter_cls", [ClaudeCodeAdapter, CodexCliAdapter])
def test_live_smoke(adapter_cls, tmp_path: Path):
    adapter = adapter_cls()
    health = adapter.health_check()
    if not health.installed:
        pytest.skip(f"{adapter.name} is not installed")
    spec = TaskSpec(
        task_id="task_live",
        run_id="run_live",
        kind=TaskKind.PLANNING,
        instruction="Reply with exactly the text: NEXUS-WORKER-OK",
        workspace=tmp_path,
        timeout_seconds=240,
        max_turns=2,
        read_only=True,
    )
    result = adapter.execute(spec)
    assert result.ok, result.summary
    assert "NEXUS-WORKER-OK" in (result.summary + result.output_text)
