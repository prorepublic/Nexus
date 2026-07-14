"""The `nexus` CLI: scriptable control of the local control plane."""

import os
import signal
import stat
import threading
from pathlib import Path

import typer
from sqlalchemy import select

from nexus.config import get_settings
from nexus.observability import configure_logging

app = typer.Typer(help="Nexus: local-first AI development orchestration.", no_args_is_help=True)
goal_app = typer.Typer(help="Manage goals.", no_args_is_help=True)
task_app = typer.Typer(help="Inspect tasks.", no_args_is_help=True)
run_app = typer.Typer(help="Inspect and control runs.", no_args_is_help=True)
worker_app = typer.Typer(help="Worker adapters.", no_args_is_help=True)
notion_app = typer.Typer(help="Notion workspace integration.", no_args_is_help=True)
domain_app = typer.Typer(help="Domain discovery skill.", no_args_is_help=True)
app.add_typer(goal_app, name="goal")
app.add_typer(task_app, name="task")
app.add_typer(run_app, name="run")
app.add_typer(worker_app, name="worker")
app.add_typer(notion_app, name="notion")
app.add_typer(domain_app, name="domain")

PID_FILE = Path.home() / ".nexus" / "control-plane.pid"


@app.callback()
def _init(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    settings = get_settings()
    configure_logging("DEBUG" if verbose else settings.log_level, settings.log_json)


# --- environment -----------------------------------------------------------
@app.command()
def doctor() -> None:
    """Check local environment health."""
    from nexus.cli.doctor import run_checks

    failed = False
    for check in run_checks():
        mark = "OK  " if check.ok else ("WARN" if check.warn else "FAIL")
        if not check.ok and not check.warn:
            failed = True
        typer.echo(f"[{mark}] {check.name:<16} {check.detail}")
    raise typer.Exit(1 if failed else 0)


@app.command()
def bootstrap() -> None:
    """Apply migrations and seed baseline records."""
    import subprocess

    cp_dir = Path(__file__).resolve().parents[3]
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=cp_dir, check=True)
    from nexus.db.base import session_scope
    from nexus.db.models import User, Worker

    with session_scope() as session:
        if not session.scalars(select(User)).first():
            session.add(User(name="Owner", role="owner"))
        for name, provider in [
            ("fake", "Nexus built-in"),
            ("claude-code", "Anthropic (Claude Max)"),
            ("codex-cli", "OpenAI (ChatGPT Plus)"),
        ]:
            if not session.scalars(select(Worker).where(Worker.name == name)).first():
                session.add(Worker(name=name, provider=provider))
    typer.echo("Bootstrap complete: schema migrated, baseline records seeded.")


# --- lifecycle ---------------------------------------------------------------
@app.command()
def start(
    foreground: bool = typer.Option(
        False, "--foreground", help="Run in the foreground (Ctrl+C to stop)."
    ),
) -> None:
    """Start the control plane API and orchestrator."""
    import uvicorn

    from nexus.services.orchestrator import Orchestrator

    settings = get_settings()
    if not foreground:
        typer.echo(
            "Background daemonization is not implemented yet; running in the "
            "foreground. Use `make dev` or a terminal multiplexer."
        )
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    orchestrator = Orchestrator()
    thread = threading.Thread(
        target=orchestrator.run_forever, daemon=True, name="nexus-orchestrator"
    )
    thread.start()
    typer.echo(
        f"Control plane on http://{settings.api_host}:{settings.api_port} (orchestrator running)"
    )
    try:
        uvicorn.run(
            "nexus.api.app:app", host=settings.api_host, port=settings.api_port, log_level="info"
        )
    finally:
        orchestrator.stop()
        PID_FILE.unlink(missing_ok=True)


@app.command()
def stop() -> None:
    """Stop a control plane started by `nexus start`."""
    if not PID_FILE.exists():
        typer.echo("No pid file found; control plane does not appear to be running.")
        raise typer.Exit(1)
    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        typer.echo(f"Sent SIGTERM to control plane (pid {pid}).")
    except ProcessLookupError:
        typer.echo("Stale pid file removed.")
    PID_FILE.unlink(missing_ok=True)


@app.command()
def status() -> None:
    """Show control plane status."""
    import httpx

    settings = get_settings()
    url = f"http://{settings.api_host}:{settings.api_port}/api/system/status"
    try:
        data = httpx.get(url, timeout=5).json()
    except httpx.HTTPError:
        typer.echo(f"Control plane unreachable at {url}. Start it with `nexus start`.")
        raise typer.Exit(1) from None
    typer.echo(f"Version:   {data['version']}")
    typer.echo(f"Database:  {data['database']}")
    typer.echo(f"Cost mode: {data['cost_mode']['description']}")
    for worker in data["workers"]:
        state = "available" if worker["available"] else "unavailable"
        typer.echo(f"Worker {worker['name']:<12} {state:<12} {worker['detail']}")


# --- goals -------------------------------------------------------------------
@goal_app.command("create")
def goal_create(
    title: str = typer.Option(..., "--title", "-t"),
    description: str = typer.Option(..., "--description", "-d"),
    repository: str | None = typer.Option(None, "--repo"),
    worker: str | None = typer.Option(
        None, "--worker", help="claude-code | codex-cli | fake (default: auto)"
    ),
    criteria: list[str] = typer.Option([], "--criterion", "-c"),
    priority: str = typer.Option("normal", "--priority"),
) -> None:
    """Create a goal and plan it into tasks."""
    from nexus.db.base import session_scope
    from nexus.db.models import Goal, Repository
    from nexus.services.events import record_audit
    from nexus.services.planner import DeterministicPlanner, create_plan

    with session_scope() as session:
        repo = None
        if repository:
            repo = session.scalars(
                select(Repository).where(Repository.name == repository)
            ).first() or Repository(name=repository)
            session.add(repo)
            session.flush()
        goal = Goal(
            title=title,
            description=description,
            priority=priority,
            requested_worker=worker,
            acceptance_criteria=list(criteria),
            repository_id=repo.id if repo else None,
        )
        session.add(goal)
        session.flush()
        create_plan(session, goal, DeterministicPlanner())
        record_audit(session, "goal.created", goal_id=goal.id, actor="owner-cli")
        typer.echo(f"Created {goal.id} with {len(goal.tasks)} task(s); status: {goal.status}")


@goal_app.command("list")
def goal_list(limit: int = 20) -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import Goal

    with session_scope() as session:
        goals = session.scalars(select(Goal).order_by(Goal.created_at.desc()).limit(limit)).all()
        if not goals:
            typer.echo("No goals yet. Create one with `nexus goal create`.")
            return
        for goal in goals:
            typer.echo(f"{goal.id}  {goal.status:<10} {goal.priority:<7} {goal.title[:70]}")


@task_app.command("list")
def task_list(status: str | None = None, limit: int = 50) -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import Task

    with session_scope() as session:
        stmt = select(Task).order_by(Task.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(Task.status == status)
        for task in session.scalars(stmt):
            typer.echo(
                f"{task.id}  {task.status:<11} {task.worker or '-':<12} "
                f"attempts={task.attempt_count} {task.title[:60]}"
            )


@run_app.command("list")
def run_list(limit: int = 50) -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import Run

    with session_scope() as session:
        for run in session.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit)):
            typer.echo(
                f"{run.id}  {run.status:<10} {run.worker:<12} "
                f"attempt={run.attempt} {(run.exit_summary or '')[:60]}"
            )


@run_app.command("inspect")
def run_inspect(run_id: str) -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import Run

    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None:
            typer.echo("run not found")
            raise typer.Exit(1)
        typer.echo(
            f"Run {run.id} task={run.task_id} worker={run.worker} "
            f"status={run.status} attempt={run.attempt}"
        )
        typer.echo(f"Summary: {run.exit_summary or '-'}")
        for event in sorted(run.events, key=lambda item: item.ts):
            typer.echo(f"  {event.ts:%H:%M:%S} [{event.type}] {event.message[:100]}")


@run_app.command("cancel")
def run_cancel(run_id: str) -> None:
    from nexus.db.base import session_scope
    from nexus.services.orchestrator import cancel_run

    with session_scope() as session:
        ok = cancel_run(session, run_id)
    typer.echo("Cancellation requested." if ok else "Run not found or already terminal.")
    raise typer.Exit(0 if ok else 1)


# --- workers -----------------------------------------------------------------
@worker_app.command("list")
def worker_list() -> None:
    from nexus.workers.registry import get_registry

    for name, health in get_registry().all_health().items():
        state = "available" if health.available else "unavailable"
        typer.echo(f"{str(name):<12} {state:<12} version={health.version or '-'} {health.detail}")


@worker_app.command("test")
def worker_test(
    name: str = typer.Argument(..., help="fake | claude-code | codex-cli"),
    live: bool = typer.Option(
        False,
        "--live",
        help="Actually invoke the worker CLI (uses your subscription; never done automatically).",
    ),
) -> None:
    """Health-check a worker; with --live, run a one-line smoke task."""
    import tempfile

    from nexus.domain.enums import TaskKind
    from nexus.workers.base import TaskSpec
    from nexus.workers.registry import get_registry

    adapter = get_registry().get(name)
    health = adapter.health_check()
    typer.echo(
        f"installed={health.installed} version={health.version} "
        f"authenticated={health.authenticated}"
    )
    typer.echo(health.detail)
    if not live:
        return
    if not health.installed:
        typer.echo("Cannot run live test: worker is not installed.")
        raise typer.Exit(1)
    with tempfile.TemporaryDirectory(prefix="nexus-worker-test-") as tmp:
        spec = TaskSpec(
            task_id="task_livetest",
            run_id="run_livetest",
            kind=TaskKind.PLANNING,
            instruction="Reply with exactly: NEXUS-WORKER-OK",
            workspace=Path(tmp),
            timeout_seconds=180,
            max_turns=2,
            read_only=True,
        )
        result = adapter.execute(spec)
    typer.echo(f"ok={result.ok} summary={result.summary[:300]}")
    raise typer.Exit(0 if result.ok else 1)


# --- notion ------------------------------------------------------------------
@notion_app.command("setup")
def notion_setup() -> None:
    """Interactively store the Notion token and parent page in .env (chmod 600)."""
    typer.echo("Create an internal integration at https://www.notion.so/my-integrations,")
    typer.echo("grant it access to a parent page, then paste the values here.")
    typer.echo("They are written only to the local .env file, which git ignores.")
    token = typer.prompt("Notion internal integration token", hide_input=True)
    parent = typer.prompt("Parent page URL or ID")
    env_path = Path.cwd() / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = [
            line
            for line in env_path.read_text().splitlines()
            if not line.startswith(("NEXUS_NOTION_TOKEN=", "NEXUS_NOTION_PARENT_PAGE="))
        ]
    lines += [f"NEXUS_NOTION_TOKEN={token}", f"NEXUS_NOTION_PARENT_PAGE={parent}"]
    env_path.write_text("\n".join(lines) + "\n")
    env_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    typer.echo(f"Saved to {env_path} with permissions 600.")


@notion_app.command("bootstrap")
def notion_bootstrap() -> None:
    """Create or reconcile the Nexus Notion workspace (idempotent)."""
    from nexus.adapters.notion import NotionClient, NotionNotConfigured, bootstrap_workspace

    settings = get_settings()
    if not settings.notion_parent_page:
        typer.echo("NEXUS_NOTION_PARENT_PAGE is not set. Run `nexus notion setup` first.")
        raise typer.Exit(1)
    try:
        client = NotionClient()
    except NotionNotConfigured as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None
    try:
        report = bootstrap_workspace(client, settings.notion_parent_page)
    finally:
        client.close()
    typer.echo(f"Created pages:      {', '.join(report.created_pages) or '(none)'}")
    typer.echo(f"Existing pages:     {', '.join(report.existing_pages) or '(none)'}")
    typer.echo(f"Created databases:  {', '.join(report.created_databases) or '(none)'}")
    typer.echo(f"Existing databases: {', '.join(report.existing_databases) or '(none)'}")


@notion_app.command("sync")
def notion_sync() -> None:
    """Push current goal and task state to the Notion databases."""
    typer.echo(
        "Structured goal/task sync to Notion databases is scaffolded but not "
        "implemented yet (see docs/ROADMAP.md). The workspace bootstrap and "
        "sync-record plumbing are in place."
    )
    raise typer.Exit(2)


# --- domain skill --------------------------------------------------------------
@domain_app.command("search")
def domain_search_cmd(
    word: str = typer.Option(..., "--word", "-w", help="Required word, e.g. nexus"),
    second: list[str] = typer.Option([], "--second", "-s", help="Optional explicit second words"),
    tone: str = typer.Option(
        "authority", "--tone", help="authority | intelligence | delivery | trust"
    ),
    count: int = typer.Option(20, "--count", "-n"),
    tld: str = typer.Option("com", "--tld"),
    export: str | None = typer.Option(None, "--export", help="Write results to a .csv or .md file"),
) -> None:
    """Generate serious name candidates and check registration status via RDAP."""
    from nexus.skills.domain_search import search_domains

    result = search_domains(word, list(second) or None, tone, count, tld)
    for item in result.lookups:
        cache_note = " (cached)" if item.from_cache else ""
        typer.echo(f"{item.domain:<28} {item.status:<18}{cache_note} {item.detail}")
    typer.echo(
        "\nNote: RDAP results are informational, not a legal guarantee. "
        "Nexus never purchases domains."
    )
    if export:
        path = Path(export)
        content = result.to_csv() if path.suffix == ".csv" else result.to_markdown()
        path.write_text(content)
        typer.echo(f"Exported to {path}")


def main() -> None:  # console-script shim used by some packagers
    app()


if __name__ == "__main__":
    app()
