import pytest

from nexus.domain.enums import TaskKind, WorkerName
from nexus.routing.router import NoWorkerAvailable, Router


def all_workers() -> set[WorkerName]:
    return {WorkerName.CLAUDE_CODE, WorkerName.CODEX_CLI, WorkerName.FAKE}


class TestDefaults:
    def test_claude_preferred_for_architecture(self):
        decision = Router(available=all_workers()).route(TaskKind.ARCHITECTURE)
        assert decision.worker == WorkerName.CLAUDE_CODE

    def test_codex_preferred_for_implementation(self):
        decision = Router(available=all_workers()).route(TaskKind.IMPLEMENTATION)
        assert decision.worker == WorkerName.CODEX_CLI

    def test_falls_back_when_preferred_unavailable(self):
        router = Router(available={WorkerName.CLAUDE_CODE, WorkerName.FAKE})
        decision = router.route(TaskKind.IMPLEMENTATION)
        assert decision.worker == WorkerName.CLAUDE_CODE


class TestCrossReview:
    def test_reviewer_differs_from_implementer(self):
        decision = Router(available=all_workers()).route(TaskKind.IMPLEMENTATION)
        assert decision.reviewer is not None
        assert decision.reviewer != decision.worker

    def test_claude_reviews_codex_work(self):
        decision = Router(available=all_workers()).route(TaskKind.IMPLEMENTATION)
        assert decision.worker == WorkerName.CODEX_CLI
        assert decision.reviewer == WorkerName.CLAUDE_CODE

    def test_codex_reviews_claude_work(self):
        router = Router(available={WorkerName.CLAUDE_CODE, WorkerName.CODEX_CLI})
        decision = router.route(TaskKind.ARCHITECTURE)
        assert decision.worker == WorkerName.CLAUDE_CODE
        assert decision.reviewer == WorkerName.CODEX_CLI

    def test_no_reviewer_when_alone(self):
        decision = Router(available={WorkerName.CLAUDE_CODE}).route(TaskKind.IMPLEMENTATION)
        assert decision.reviewer is None

    def test_fake_never_reviews_real_worker(self):
        router = Router(available={WorkerName.CLAUDE_CODE, WorkerName.FAKE})
        decision = router.route(TaskKind.ARCHITECTURE)
        assert decision.worker == WorkerName.CLAUDE_CODE
        assert decision.reviewer is None


class TestOverrides:
    def test_owner_override_wins(self):
        decision = Router(available=all_workers()).route(
            TaskKind.IMPLEMENTATION, requested_worker=WorkerName.FAKE
        )
        assert decision.worker == WorkerName.FAKE
        assert decision.reason == "owner override"

    def test_override_to_unavailable_worker_fails(self):
        router = Router(available={WorkerName.FAKE})
        with pytest.raises(NoWorkerAvailable):
            router.route(TaskKind.IMPLEMENTATION, requested_worker=WorkerName.CODEX_CLI)

    def test_no_workers_at_all(self):
        with pytest.raises(NoWorkerAvailable):
            Router(available=set()).route(TaskKind.IMPLEMENTATION)
