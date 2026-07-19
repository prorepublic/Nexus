from pathlib import Path

from nexus.domain.enums import TaskKind
from nexus.workers.base import TaskSpec, WorkerEvent
from nexus.workers.claude_code import ClaudeCodeAdapter
from nexus.workers.codex_cli import CodexCliAdapter

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _collect(events: list[WorkerEvent]):
    def emit(event: WorkerEvent) -> None:
        events.append(event)

    return emit


class TestClaudeParser:
    def test_success_stream(self):
        adapter = ClaudeCodeAdapter()
        events: list[WorkerEvent] = []
        result = adapter.parse_result(
            (FIXTURES / "claude_stream.jsonl").read_text(), "", 0, events, _collect(events)
        )
        assert result.ok
        assert result.session_ref == "sess-abc123"
        assert "hello.py" in result.summary
        tool_events = [e for e in events if e.type == "tool"]
        assert {e.message for e in tool_events} == {"Write", "Bash"}

    def test_error_stream(self):
        adapter = ClaudeCodeAdapter()
        events: list[WorkerEvent] = []
        result = adapter.parse_result(
            (FIXTURES / "claude_stream_error.jsonl").read_text(),
            "",
            1,
            events,
            _collect(events),
        )
        assert not result.ok
        assert result.error_category == "unknown"

    def test_auth_error_categorized(self):
        adapter = ClaudeCodeAdapter()
        events: list[WorkerEvent] = []
        result = adapter.parse_result(
            "", "Error: please log in with `claude login`", 1, events, _collect(events)
        )
        assert not result.ok
        assert result.error_category == "auth"

    def test_garbage_output_does_not_crash(self):
        adapter = ClaudeCodeAdapter()
        events: list[WorkerEvent] = []
        result = adapter.parse_result("not json\n{broken\n", "", 0, events, _collect(events))
        assert result.ok  # exit code 0 wins when no result object exists

    def test_argv_never_bypasses_permissions(self, tmp_path: Path):
        adapter = ClaudeCodeAdapter()
        spec = TaskSpec(
            task_id="t",
            run_id="r",
            kind=TaskKind.IMPLEMENTATION,
            instruction="do something",
            workspace=tmp_path,
        )
        argv = adapter.build_argv(spec)
        assert "--dangerously-skip-permissions" not in argv
        assert "--max-turns" in argv
        assert str(tmp_path) in argv

    def test_read_only_tasks_get_read_only_tools(self, tmp_path: Path):
        adapter = ClaudeCodeAdapter()
        spec = TaskSpec(
            task_id="t",
            run_id="r",
            kind=TaskKind.REVIEW,
            instruction="review",
            workspace=tmp_path,
            read_only=True,
        )
        argv = adapter.build_argv(spec)
        tools = argv[argv.index("--allowedTools") + 1]
        assert "Edit" not in tools and "Write" not in tools


class TestCodexParser:
    def test_wrapped_events(self):
        adapter = CodexCliAdapter()
        events: list[WorkerEvent] = []
        result = adapter.parse_result(
            (FIXTURES / "codex_events.jsonl").read_text(), "", 0, events, _collect(events)
        )
        assert result.ok
        assert result.session_ref == "th_789"
        assert "parser fix" in result.summary
        assert any(e.type == "command" and "pytest" in e.message for e in events)

    def test_flat_events(self):
        adapter = CodexCliAdapter()
        events: list[WorkerEvent] = []
        result = adapter.parse_result(
            (FIXTURES / "codex_events_flat.jsonl").read_text(),
            "",
            0,
            events,
            _collect(events),
        )
        assert result.ok
        assert result.session_ref == "th_flat_1"
        assert "Refactor complete" in result.summary

    def test_auth_failure_categorized(self):
        adapter = CodexCliAdapter()
        events: list[WorkerEvent] = []
        result = adapter.parse_result(
            "", "error: not logged in, run codex login", 1, events, _collect(events)
        )
        assert not result.ok
        assert result.error_category == "auth"

    def test_sandbox_defaults(self, tmp_path: Path):
        adapter = CodexCliAdapter()
        write_spec = TaskSpec(
            task_id="t",
            run_id="r",
            kind=TaskKind.IMPLEMENTATION,
            instruction="implement",
            workspace=tmp_path,
        )
        review_spec = TaskSpec(
            task_id="t",
            run_id="r",
            kind=TaskKind.REVIEW,
            instruction="review",
            workspace=tmp_path,
            read_only=True,
        )
        assert "workspace-write" in adapter.build_argv(write_spec)
        assert "read-only" in adapter.build_argv(review_spec)
        assert "danger-full-access" not in " ".join(adapter.build_argv(write_spec))

    def test_missing_cli_reports_not_installed(self, tmp_path: Path):
        adapter = CodexCliAdapter(binary="definitely-not-a-real-binary-xyz")
        health = adapter.health_check()
        assert not health.installed
        result = adapter.execute(
            TaskSpec(
                task_id="t",
                run_id="r",
                kind=TaskKind.IMPLEMENTATION,
                instruction="x",
                workspace=tmp_path,
            )
        )
        assert not result.ok
        assert result.error_category == "not-installed"
