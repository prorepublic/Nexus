"""Local service management (ADR-012).

Reliable local operation without paid process managers:
- foreground mode for development;
- detached background mode with pid/state tracking and log files;
- macOS launchd integration for start-at-login operation;
- crash recovery via lease-based stale-run reconciliation (queue module).

The API binds to localhost only; nothing here exposes a public interface.
"""

import os
import plistlib
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from nexus.config import get_settings
from nexus.observability import get_logger

log = get_logger(__name__)

STATE_DIR = Path.home() / ".nexus"
PID_FILE = STATE_DIR / "control-plane.pid"
LOG_DIR = STATE_DIR / "logs"
LOG_FILE = LOG_DIR / "control-plane.log"
LAUNCHD_LABEL = "com.nexus.control-plane"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


@dataclass
class ServiceState:
    running: bool
    pid: int | None
    detail: str


def read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def service_state() -> ServiceState:
    pid = read_pid()
    if pid is None:
        return ServiceState(False, None, "no pid file; not started via `nexus start`")
    if _pid_alive(pid):
        return ServiceState(True, pid, f"running (pid {pid})")
    return ServiceState(False, pid, f"stale pid file (pid {pid} is dead); run `nexus recover`")


def write_pid(pid: int | None = None) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid or os.getpid()))


def clear_pid() -> None:
    PID_FILE.unlink(missing_ok=True)


def start_detached() -> int:
    """Start the control plane as a detached background process with logs."""
    from nexus.execution.runner import spawn_detached

    state = service_state()
    if state.running:
        raise RuntimeError(f"control plane already running (pid {state.pid})")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    pid = spawn_detached(
        [sys.executable, "-m", "nexus.cli.main", "start", "--foreground", "--managed"],
        cwd=Path(__file__).resolve().parents[3],
        log_file=LOG_FILE,
    )
    write_pid(pid)
    log.info("service.started_detached", pid=pid)
    return pid


def stop_service(timeout_seconds: float = 15.0) -> bool:
    import time

    pid = read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        clear_pid()
        return False
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            clear_pid()
            return True
        time.sleep(0.2)
    os.kill(pid, signal.SIGKILL)
    clear_pid()
    return True


def tail_logs(lines: int = 100) -> str:
    try:
        content = LOG_FILE.read_text()
    except OSError:
        return "(no log file yet; start the control plane with `nexus start`)"
    return "\n".join(content.splitlines()[-lines:])


def launchd_plist_content() -> dict:
    cp_dir = Path(__file__).resolve().parents[3]
    settings = get_settings()
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "nexus.cli.main",
            "start",
            "--foreground",
            "--managed",
        ],
        "WorkingDirectory": str(cp_dir),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # restart on crash, not clean exit
        "StandardOutPath": str(LOG_FILE),
        "StandardErrorPath": str(LOG_FILE),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
            "HOME": str(Path.home()),
            "NEXUS_API_PORT": str(settings.api_port),
        },
    }


def install_service() -> Path:
    """Write and load the launchd agent (macOS)."""
    if sys.platform != "darwin":
        raise RuntimeError("launchd service install is only supported on macOS")
    LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LAUNCHD_PLIST.write_bytes(plistlib.dumps(launchd_plist_content()))
    from nexus.execution import get_profile, get_runner
    from nexus.execution.runner import spawn_detached  # noqa: F401  (import check)

    result = get_runner().run(
        get_profile("service-local"),
        ["launchctl", "load", "-w", str(LAUNCHD_PLIST)],
        cwd=Path.home(),
    )
    if not result.ok:
        raise RuntimeError(f"launchctl load failed: {result.stderr[:300]}")
    return LAUNCHD_PLIST


def uninstall_service() -> bool:
    if not LAUNCHD_PLIST.exists():
        return False
    from nexus.execution import get_profile, get_runner

    get_runner().run(
        get_profile("service-local"),
        ["launchctl", "unload", "-w", str(LAUNCHD_PLIST)],
        cwd=Path.home(),
    )
    LAUNCHD_PLIST.unlink(missing_ok=True)
    return True
