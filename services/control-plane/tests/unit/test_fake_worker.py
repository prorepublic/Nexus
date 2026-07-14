from pathlib import Path

from nexus.domain.enums import TaskKind
from nexus.workers.base import TaskSpec
from nexus.workers.fake import FakeWorker


def spec(
    tmp_path: Path, instruction: str, task_id: str = "task_1", run_id: str = "run_1"
) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        run_id=run_id,
        kind=TaskKind.IMPLEMENTATION,
        instruction=instruction,
        workspace=tmp_path,
    )


class TestFakeWorker:
    def test_deterministic_success_writes_file(self, tmp_path: Path):
        worker = FakeWorker()
        result = worker.execute(spec(tmp_path, "do the thing [fake:write=out/result.md]"))
        assert result.ok
        assert (tmp_path / "out" / "result.md").exists()

    def test_always_fail_directive(self, tmp_path: Path):
        worker = FakeWorker()
        result = worker.execute(spec(tmp_path, "[fake:fail] break"))
        assert not result.ok
        assert result.error_category == "simulated"

    def test_fail_first_then_succeed(self, tmp_path: Path):
        worker = FakeWorker()
        task_spec = spec(tmp_path, "[fake:fail-first=2] flaky")
        assert not worker.execute(task_spec).ok
        assert not worker.execute(task_spec).ok
        assert worker.execute(task_spec).ok

    def test_read_only_writes_nothing(self, tmp_path: Path):
        worker = FakeWorker()
        task_spec = TaskSpec(
            task_id="t",
            run_id="r",
            kind=TaskKind.REVIEW,
            instruction="review it",
            workspace=tmp_path,
            read_only=True,
        )
        result = worker.execute(task_spec)
        assert result.ok
        assert list(tmp_path.iterdir()) == []

    def test_path_escape_refused(self, tmp_path: Path):
        worker = FakeWorker()
        result = worker.execute(spec(tmp_path, "[fake:write=../../etc/evil]"))
        assert not result.ok
        assert result.error_category == "policy"

    def test_cancellation_during_sleep(self, tmp_path: Path):
        import threading

        worker = FakeWorker()
        task_spec = spec(tmp_path, "[fake:sleep=10] slow")
        timer = threading.Timer(0.2, lambda: worker.cancel("run_1"))
        timer.start()
        result = worker.execute(task_spec)
        timer.cancel()
        assert not result.ok
        assert result.error_category == "cancelled"

    def test_health_is_always_available(self):
        health = FakeWorker().health_check()
        assert health.available
        assert health.authenticated
