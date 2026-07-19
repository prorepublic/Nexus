# ADR-012: Local service management without a paid process manager

- Status: accepted
- Date: 2026-07-15

## Context

The bootstrap control plane ran only in the foreground: closing the terminal killed orchestration, there was no restart-on-crash story, and crash recovery was manual. A local-first system that autonomously executes multi-hour goals needs to survive terminal closure and crashes — but adding a paid process manager or a heavyweight supervisor would contradict the cost policy and the single-dependency posture.

## Decision

Service management is built in (`src/nexus/services/service_mgmt.py`, CLI in `cli/main.py`):

- **`nexus start` runs in the background by default**: the CLI relaunches itself detached (`spawn_detached`, which only ever launches the `nexus.cli.main` module, in its own process group), records the pid in `~/.nexus/control-plane.pid`, and appends output to `~/.nexus/logs/control-plane.log`. `nexus start --foreground` remains the development mode.
- **`nexus stop`** SIGTERMs the recorded pid (whole process group, SIGKILL after a grace period), **`nexus restart`** cycles it, **`nexus logs`** tails the managed log file, and **`nexus status`** reports liveness via the API.
- **`nexus recover`** reconciles after a crash: clears a stale pid file, fails lease-expired runs as infrastructure and requeues them within budget (ADR-010), and enqueues any ready tasks.
- **launchd integration (macOS)**: `nexus install-service` writes and loads `~/Library/LaunchAgents/com.nexus.control-plane.plist` with `RunAtLoad` and `KeepAlive` on non-successful exit, so Nexus starts at login and restarts on crash but stays stopped after a clean `nexus stop`-style exit. `nexus uninstall-service` unloads and removes it. `launchctl` itself is invoked through the `service-local` execution profile (load/unload/list only).
- The API still binds to localhost only; background operation changes process lifecycle, not network exposure (ADR-013).

## Consequences

- Orchestration survives terminal closure, crashes (KeepAlive), and reboots (RunAtLoad) with zero new dependencies and zero cost.
- Crash recovery is a single idempotent command, and most of it also runs automatically on every orchestrator tick — `nexus recover` is for the cases where the whole process was down.
- Pid-file management is honest: a stale pid is detected and reported (`run nexus recover`), never silently reused.
- launchd support is macOS-only; other platforms use background mode plus their own supervision. Accepted: the owner's machine is a Mac.
- Logs are plain append-only files under `~/.nexus/logs` with the same redaction rules as all other output paths.
