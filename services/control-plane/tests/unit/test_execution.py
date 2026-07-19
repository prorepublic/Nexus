"""Security-focused tests for the centralized execution subsystem."""

import time
from pathlib import Path

import pytest

from nexus.execution import (
    ExecutionProfile,
    PathEscapeError,
    ProcessRunner,
    ProfileViolation,
    confine_path,
    get_profile,
)
from nexus.execution.paths import confine_cwd

PY_PROFILE = ExecutionProfile(
    name="test-python",
    executables=frozenset({"python3"}),
    timeout_seconds=10,
    max_output_bytes=10_000,
    env_passthrough=("PATH", "HOME"),
)


class TestPathConfinement:
    def test_relative_path_ok(self, tmp_path: Path):
        assert confine_path("src/a.py", tmp_path) == tmp_path.resolve() / "src" / "a.py"

    def test_parent_traversal_rejected(self, tmp_path: Path):
        with pytest.raises(PathEscapeError, match="parent traversal"):
            confine_path("../outside.txt", tmp_path)

    def test_embedded_traversal_rejected(self, tmp_path: Path):
        with pytest.raises(PathEscapeError, match="parent traversal"):
            confine_path("src/../../outside.txt", tmp_path)

    def test_absolute_outside_rejected(self, tmp_path: Path):
        with pytest.raises(PathEscapeError, match="outside"):
            confine_path("/etc/passwd", tmp_path)

    def test_absolute_inside_ok(self, tmp_path: Path):
        target = tmp_path / "inner.txt"
        assert confine_path(str(target), tmp_path) == target.resolve()

    def test_symlink_escape_rejected(self, tmp_path: Path):
        outside = tmp_path.parent / "outside-dir"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "sneaky"
        link.symlink_to(outside)
        with pytest.raises(PathEscapeError):
            confine_path("sneaky/file.txt", tmp_path)

    def test_symlink_file_escape_rejected(self, tmp_path: Path):
        secret = tmp_path.parent / "secret.txt"
        secret.write_text("s")
        link = tmp_path / "alias.txt"
        link.symlink_to(secret)
        with pytest.raises(PathEscapeError):
            confine_path("alias.txt", tmp_path)

    def test_cwd_confinement(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        assert confine_cwd(ws, [tmp_path]) == ws.resolve()
        with pytest.raises(PathEscapeError):
            confine_cwd(tmp_path.parent, [ws])


class TestProfiles:
    def test_unlisted_executable_refused(self):
        with pytest.raises(ProfileViolation, match="executable"):
            get_profile("git-readonly").check(["curl", "http://x"])

    @pytest.mark.parametrize(
        "argv",
        [
            ["git", "push", "--force"],
            ["git", "push", "origin", "main", "--force"],
            ["git", "push", "-f", "origin", "main"],
            ["git", "push", "--force-with-lease", "origin", "main"],
            ["git", "push", "--force=true"],
            ["git", "push", "--mirror"],
            ["git", "push", "--delete", "origin", "branch"],
        ],
    )
    def test_git_push_destructive_flags_refused_any_order(self, argv):
        with pytest.raises(ProfileViolation, match="prohibited"):
            get_profile("git-push").check(argv)

    def test_git_push_safe_form_allowed(self):
        get_profile("git-push").check(["git", "push", "-u", "origin", "nexus/a/b"])

    def test_claude_permission_bypass_refused(self):
        with pytest.raises(ProfileViolation, match="prohibited"):
            get_profile("worker-claude").check(
                ["claude", "-p", "x", "--dangerously-skip-permissions"]
            )

    def test_codex_full_access_refused(self):
        with pytest.raises(ProfileViolation, match="prohibited"):
            get_profile("worker-codex").check(
                ["codex", "exec", "--sandbox", "danger-full-access", "x"]
            )

    def test_codex_subcommand_restricted(self):
        with pytest.raises(ProfileViolation, match="subcommand"):
            get_profile("worker-codex").check(["codex", "apply", "something"])

    def test_gh_merge_never_allowed(self):
        for profile_name in ("github-readonly", "github-write-safe"):
            with pytest.raises(ProfileViolation):
                get_profile(profile_name).check(["gh", "pr", "merge", "1"])

    def test_gh_readonly_refuses_write_methods(self):
        with pytest.raises(ProfileViolation):
            get_profile("github-readonly").check(["gh", "api", "repos/o/r/issues", "-X", "POST"])

    def test_gh_write_safe_allows_draft_pr(self):
        get_profile("github-write-safe").check(
            ["gh", "pr", "create", "--draft", "--title", "t", "--body", "b"]
        )

    def test_untrusted_validation_profile_permits_nothing(self):
        profile = get_profile("validation-untrusted")
        result = ProcessRunner().run(profile, ["make", "test"], cwd=Path("/tmp"))
        assert result.exit_code is None
        assert "no host execution" in (result.error or "")


class TestRunner:
    def test_basic_execution_and_exit_code(self, tmp_path: Path):
        result = ProcessRunner().run(PY_PROFILE, ["python3", "-c", "print('hi')"], cwd=tmp_path)
        assert result.ok
        assert "hi" in result.stdout

    def test_env_is_filtered(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("NEXUS_NOTION_TOKEN", "ntn_" + "x" * 40)
        monkeypatch.setenv("SUPER_SECRET", "leakme")
        result = ProcessRunner().run(
            PY_PROFILE,
            ["python3", "-c", "import os,json;print(json.dumps(dict(os.environ)))"],
            cwd=tmp_path,
        )
        assert "SUPER_SECRET" not in result.stdout
        assert "NEXUS_NOTION_TOKEN" not in result.stdout
        assert "PATH" in result.stdout

    def test_output_redaction(self, tmp_path: Path):
        result = ProcessRunner().run(
            PY_PROFILE,
            ["python3", "-c", "print('token ghp_" + "a" * 36 + "')"],
            cwd=tmp_path,
        )
        assert "ghp_" not in result.stdout
        assert "[REDACTED]" in result.stdout

    def test_output_cap(self, tmp_path: Path):
        result = ProcessRunner().run(
            PY_PROFILE, ["python3", "-c", "print('x' * 100000)"], cwd=tmp_path
        )
        assert result.truncated
        assert len(result.stdout) <= 11_000

    def test_streaming_lines(self, tmp_path: Path):
        lines: list[str] = []
        ProcessRunner().run(
            PY_PROFILE,
            ["python3", "-c", "print('a'); print('b')"],
            cwd=tmp_path,
            on_line=lines.append,
        )
        assert lines == ["a", "b"]

    def test_timeout_kills_process_group(self, tmp_path: Path):
        """The parent AND its spawned child must both die on timeout."""
        marker = tmp_path / "child-alive.txt"
        child_code = "import time,sys;time.sleep(5);open(sys.argv[1],'w').write('leak')"
        script = (
            "import subprocess,sys,time\n"
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}, {str(marker)!r}])\n"
            "time.sleep(30)\n"
        )
        start = time.monotonic()
        result = ProcessRunner().run(PY_PROFILE, ["python3", "-c", script], cwd=tmp_path, timeout=2)
        assert result.timed_out
        assert time.monotonic() - start < 10
        time.sleep(4)  # give the grandchild time to write the marker if it survived
        assert not marker.exists(), "grandchild process survived group termination"

    def test_cancellation(self, tmp_path: Path):
        import threading

        runner = ProcessRunner()

        def cancel_soon():
            time.sleep(0.5)
            assert runner.cancel("job-1")

        thread = threading.Thread(target=cancel_soon)
        thread.start()
        start = time.monotonic()
        result = runner.run(
            PY_PROFILE,
            ["python3", "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            cancel_key="job-1",
        )
        thread.join()
        assert result.cancelled
        assert time.monotonic() - start < 10

    def test_cwd_confined(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        with pytest.raises(PathEscapeError):
            ProcessRunner().run(
                PY_PROFILE, ["python3", "-c", "1"], cwd=tmp_path, permitted_roots=[ws]
            )

    def test_audit_recorder_called(self, tmp_path: Path):
        records = []

        def recorder(profile, argv, cwd, exit_code, duration_ms, truncated, run_id):
            records.append((profile, argv[0], exit_code, run_id))

        ProcessRunner(recorder=recorder).run(
            PY_PROFILE, ["python3", "-c", "1"], cwd=tmp_path, run_id="run_x"
        )
        assert records == [("test-python", "python3", 0, "run_x")]


class TestNoParallelExecutionPaths:
    def test_no_direct_subprocess_outside_execution_subsystem(self):
        """Direct subprocess usage is confined to nexus/execution/runner.py.

        The github adapter imports subprocess only for the CompletedProcess
        type used by its injectable test runner; Popen/run/call are refused.
        """
        src_root = Path(__file__).parents[2] / "src" / "nexus"
        offenders: list[str] = []
        for path in src_root.rglob("*.py"):
            rel = path.relative_to(src_root).as_posix()
            if rel == "execution/runner.py":
                continue
            content = path.read_text()
            for needle in (
                "subprocess.run(",
                "subprocess.Popen(",
                "subprocess.call(",
                "os.system(",
                "os.popen(",
            ):
                if needle in content:
                    offenders.append(f"{rel}: {needle}")
        assert not offenders, f"direct process execution found: {offenders}"
