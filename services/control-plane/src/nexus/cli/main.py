"""The `nexus` CLI: scriptable control of the local control plane."""

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
repo_app = typer.Typer(help="Repository registration and onboarding.", no_args_is_help=True)
approval_app = typer.Typer(help="Owner approval gates.", no_args_is_help=True)
github_app = typer.Typer(help="GitHub delivery and feedback.", no_args_is_help=True)
app.add_typer(goal_app, name="goal")
app.add_typer(task_app, name="task")
app.add_typer(run_app, name="run")
app.add_typer(worker_app, name="worker")
app.add_typer(notion_app, name="notion")
app.add_typer(domain_app, name="domain")
app.add_typer(repo_app, name="repo")
app.add_typer(approval_app, name="approval")
app.add_typer(github_app, name="github")

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
    from nexus.execution import get_profile, get_runner

    cp_dir = Path(__file__).resolve().parents[3]
    migration = get_runner().run(
        get_profile("migration-local"),
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=cp_dir,
        permitted_roots=[cp_dir],
    )
    if not migration.ok:
        typer.echo(f"Migration failed: {migration.stderr[-500:]}")
        raise typer.Exit(1)
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
    managed: bool = typer.Option(
        False,
        "--managed",
        hidden=True,
        help="Internal: launched by service management; skips pid handling.",
    ),
) -> None:
    """Start the control plane API and orchestrator (background by default)."""
    from nexus.services import service_mgmt

    settings = get_settings()
    if not foreground:
        try:
            pid = service_mgmt.start_detached()
        except RuntimeError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from None
        typer.echo(
            f"Control plane started in the background (pid {pid}). "
            f"API: http://{settings.api_host}:{settings.api_port} — "
            "`nexus logs` to follow, `nexus stop` to stop."
        )
        return

    import uvicorn

    from nexus.services.orchestrator import Orchestrator

    if not managed:
        service_mgmt.write_pid()
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
        if not managed:
            service_mgmt.clear_pid()


@app.command()
def stop() -> None:
    """Stop a control plane started by `nexus start`."""
    from nexus.services import service_mgmt

    if service_mgmt.stop_service():
        typer.echo("Control plane stopped.")
    else:
        typer.echo("No running control plane found (no or stale pid file).")
        raise typer.Exit(1)


@app.command()
def restart() -> None:
    """Restart the background control plane."""
    from nexus.services import service_mgmt

    service_mgmt.stop_service()
    pid = service_mgmt.start_detached()
    typer.echo(f"Control plane restarted (pid {pid}).")


@app.command()
def logs(lines: int = typer.Option(100, "--lines", "-n")) -> None:
    """Show recent control plane logs."""
    from nexus.services import service_mgmt

    typer.echo(service_mgmt.tail_logs(lines))


@app.command()
def recover() -> None:
    """Reconcile stale runs and abandoned state after a crash or restart."""
    from nexus.db.base import session_scope
    from nexus.services import service_mgmt
    from nexus.services.queue import enqueue_ready_tasks, recover_stale_runs

    state = service_mgmt.service_state()
    if not state.running and state.pid is not None:
        service_mgmt.clear_pid()
        typer.echo(f"Cleared stale pid file (pid {state.pid}).")
    with session_scope() as session:
        recovered = recover_stale_runs(session)
        enqueued = enqueue_ready_tasks(session)
    typer.echo(f"Recovered {recovered} stale run(s); enqueued {enqueued} ready task(s).")


@app.command("install-service")
def install_service_cmd() -> None:
    """Install the launchd agent so Nexus starts at login (macOS)."""
    from nexus.services import service_mgmt

    try:
        plist = service_mgmt.install_service()
    except RuntimeError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None
    typer.echo(f"Installed and loaded {plist}. Logs: {service_mgmt.LOG_FILE}")


@app.command("uninstall-service")
def uninstall_service_cmd() -> None:
    """Remove the launchd agent."""
    from nexus.services import service_mgmt

    if service_mgmt.uninstall_service():
        typer.echo("Service unloaded and removed.")
    else:
        typer.echo("No installed service found.")


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
    """Push repositories, goals, plans, tasks, runs, approvals, PRs, and
    findings to the Notion databases (idempotent, one-way DB -> Notion)."""
    from nexus.adapters.notion import NotionClient, NotionNotConfigured
    from nexus.db.base import session_scope
    from nexus.services.notion_sync import sync_all

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
        with session_scope() as session:
            report = sync_all(session, client, settings.notion_parent_page)
    finally:
        client.close()
    for kind in sorted(set(report.created) | set(report.updated)):
        typer.echo(
            f"{kind:<16} created={report.created.get(kind, 0):<4} "
            f"updated={report.updated.get(kind, 0)}"
        )
    if report.total_errors:
        typer.echo(f"Errors ({report.total_errors}):")
        for kind, errors in report.errors.items():
            for error in errors[:5]:
                typer.echo(f"  {kind}: {error}")
        raise typer.Exit(1)
    typer.echo("Notion sync complete.")


@notion_app.command("reconcile")
def notion_reconcile() -> None:
    """Alias for `nexus notion sync` (sync is idempotent and self-healing)."""
    notion_sync()


@notion_app.command("doctor")
def notion_doctor() -> None:
    """Verify Notion credentials and workspace access."""
    from nexus.adapters.notion import NotionClient, NotionError, normalize_page_id

    settings = get_settings()
    if not settings.notion_token:
        typer.echo("[FAIL] token         not configured (run `nexus notion setup`)")
        raise typer.Exit(1)
    typer.echo("[OK  ] token         configured (value not shown)")
    if not settings.notion_parent_page:
        typer.echo("[FAIL] parent page   not configured")
        raise typer.Exit(1)
    try:
        parent_id = normalize_page_id(settings.notion_parent_page)
        client = NotionClient()
        try:
            client.list_child_blocks(parent_id)
        finally:
            client.close()
    except NotionError as exc:
        typer.echo(f"[FAIL] access        {str(exc)[:200]}")
        raise typer.Exit(1) from None
    typer.echo("[OK  ] access        parent page reachable with this integration")


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


# --- repositories ---------------------------------------------------------------
@repo_app.command("add")
def repo_add(
    source: str = typer.Argument(..., help="Local path, owner/repo, or GitHub URL"),
    name: str | None = typer.Option(None, "--name"),
) -> None:
    """Register a repository for Nexus-managed goals."""
    from nexus.db.base import session_scope
    from nexus.services.repositories import RepositoryError, register_repository

    try:
        with session_scope() as session:
            repo = register_repository(session, source, name=name)
            typer.echo(
                f"Registered {repo.name} ({repo.local_path})\n"
                f"  default branch: {repo.default_branch}\n"
                f"  languages:      {', '.join(str(x) for x in repo.languages) or 'unknown'}\n"
                f"  trust level:    {repo.trust_level} (raise with `nexus repo trust`)\n"
                f"  validation:     {', '.join(sorted(repo.validation_profile or {})) or 'none'}\n"
                f"  onboarded:      {repo.onboarded}"
            )
    except RepositoryError as exc:
        typer.echo(f"Registration failed: {exc}")
        raise typer.Exit(1) from None


@repo_app.command("list")
def repo_list() -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import Repository

    with session_scope() as session:
        repos = session.scalars(select(Repository).order_by(Repository.name)).all()
        if not repos:
            typer.echo("No repositories registered. Add one with `nexus repo add <source>`.")
            return
        for repo in repos:
            typer.echo(
                f"{repo.name:<40} trust={repo.trust_level:<24} "
                f"onboarded={repo.onboarded} {repo.local_path or ''}"
            )


@repo_app.command("inspect")
def repo_inspect(name: str = typer.Argument(...)) -> None:
    from nexus.db.base import session_scope
    from nexus.services.repositories import RepositoryError, get_repository

    with session_scope() as session:
        try:
            repo = get_repository(session, name)
        except RepositoryError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from None
        typer.echo(f"Name:            {repo.name}")
        typer.echo(f"Local path:      {repo.local_path}")
        typer.echo(f"Remote:          {repo.remote_url or '(none)'}")
        typer.echo(f"GitHub:          {repo.github_slug or '(none)'}")
        typer.echo(f"Default branch:  {repo.default_branch}")
        typer.echo(f"Trust level:     {repo.trust_level}")
        typer.echo(f"Languages:       {', '.join(str(x) for x in repo.languages) or 'unknown'}")
        typer.echo(f"Onboarded:       {repo.onboarded}")
        typer.echo(
            f"Protected paths: {', '.join(str(x) for x in repo.protected_paths) or '(none)'}"
        )
        typer.echo("Validation profile:")
        for kind, entry in (repo.validation_profile or {}).items():
            typer.echo(f"  {kind}: {' '.join(entry.get('command', []))}")


@repo_app.command("doctor")
def repo_doctor(name: str = typer.Argument(...)) -> None:
    """Run onboarding checks for a registered repository."""
    from nexus.db.base import session_scope
    from nexus.services.repositories import (
        RepositoryError,
        doctor_repository,
        get_repository,
    )

    with session_scope() as session:
        try:
            repo = get_repository(session, name)
        except RepositoryError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from None
        problems = doctor_repository(repo, session)
        repo.onboarded = not problems
        if problems:
            for problem in problems:
                typer.echo(f"[FAIL] {problem}")
            raise typer.Exit(1)
        typer.echo("[OK  ] repository passes onboarding checks")


@repo_app.command("trust")
def repo_trust(
    name: str = typer.Argument(...),
    level: str = typer.Argument(
        ..., help="untrusted | reviewed | trusted-local | trusted-owner-approved"
    ),
) -> None:
    """Set the repository trust level (controls host execution of repo scripts)."""
    from nexus.db.base import session_scope
    from nexus.services.events import record_audit
    from nexus.services.repositories import RepositoryError, set_trust

    try:
        with session_scope() as session:
            repo = set_trust(session, name, level)
            record_audit(
                session,
                "repository.trust-changed",
                actor="owner-cli",
                metadata={"name": repo.name, "level": level},
            )
            typer.echo(f"{repo.name} trust level set to {level}.")
    except RepositoryError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None


validation_app = typer.Typer(help="Repository validation profiles.", no_args_is_help=True)
repo_app.add_typer(validation_app, name="validation")


@validation_app.command("show")
def repo_validation_show(name: str = typer.Argument(...)) -> None:
    import json as json_module

    from nexus.db.base import session_scope
    from nexus.services.repositories import RepositoryError, get_repository

    with session_scope() as session:
        try:
            repo = get_repository(session, name)
        except RepositoryError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from None
        typer.echo(json_module.dumps(repo.validation_profile or {}, indent=2))


@validation_app.command("set")
def repo_validation_set(
    name: str = typer.Argument(...),
    kind: str = typer.Argument(..., help="e.g. tests, lint, typecheck, build"),
    command: list[str] = typer.Argument(..., help="Command argv, e.g. uv run pytest -q"),
) -> None:
    from nexus.db.base import session_scope
    from nexus.services.repositories import RepositoryError, get_repository
    from nexus.services.validation import InvalidValidationProfile, validate_profile_config

    with session_scope() as session:
        try:
            repo = get_repository(session, name)
            profile = dict(repo.validation_profile or {})
            profile[kind] = {"command": list(command)}
            validate_profile_config(profile)
            repo.validation_profile = profile
        except (RepositoryError, InvalidValidationProfile) as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from None
        typer.echo(f"{name}: {kind} -> {' '.join(command)}")


# --- approvals ---------------------------------------------------------------
@approval_app.command("list")
def approval_list(state: str = typer.Option("pending", "--state")) -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import Approval

    with session_scope() as session:
        approvals = session.scalars(
            select(Approval).where(Approval.state == state).order_by(Approval.created_at)
        ).all()
        if not approvals:
            typer.echo(f"No {state} approvals.")
            return
        for approval in approvals:
            typer.echo(
                f"{approval.id}  [{approval.risk:<6}] {approval.kind:<38} "
                f"{approval.description[:80]}"
            )


@approval_app.command("inspect")
def approval_inspect(approval_id: str) -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import Approval

    with session_scope() as session:
        approval = session.get(Approval, approval_id)
        if approval is None:
            typer.echo("approval not found")
            raise typer.Exit(1)
        typer.echo(f"Kind:        {approval.kind}")
        typer.echo(f"Risk:        {approval.risk}")
        typer.echo(f"State:       {approval.state}")
        typer.echo(f"Goal/Task:   {approval.goal_id or '-'} / {approval.task_id or '-'}")
        typer.echo(f"Requested:   {approval.created_at}")
        typer.echo(f"Description: {approval.description}")
        if approval.decision_note:
            typer.echo(f"Note:        {approval.decision_note}")


def _decide(approval_id: str, decision: str, note: str | None) -> None:
    from nexus.db.base import session_scope
    from nexus.services.approvals import decide_approval

    try:
        with session_scope() as session:
            approval = decide_approval(session, approval_id, decision, note)
            typer.echo(f"Approval {approval.id} ({approval.kind}): {approval.state}.")
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None


@approval_app.command("approve")
def approval_approve(approval_id: str, note: str | None = typer.Option(None, "--note")) -> None:
    """Approve a pending gate; the blocked operation resumes deterministically."""
    _decide(approval_id, "approved", note)


@approval_app.command("deny")
def approval_deny(approval_id: str, note: str | None = typer.Option(None, "--note")) -> None:
    """Deny a pending gate; the gated work is cancelled and never re-asked."""
    _decide(approval_id, "denied", note)


# --- goal extensions -------------------------------------------------------------
@goal_app.command("inspect")
def goal_inspect(goal_id: str) -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import ExecutionPlan, Goal, Task

    with session_scope() as session:
        goal = session.get(Goal, goal_id)
        if goal is None:
            typer.echo("goal not found")
            raise typer.Exit(1)
        typer.echo(f"Goal:     {goal.title}")
        typer.echo(
            f"Status:   {goal.status}  autonomy={goal.autonomy}  "
            f"worker={goal.requested_worker or 'auto'}"
        )
        plan = session.scalars(
            select(ExecutionPlan).where(ExecutionPlan.goal_id == goal_id)
        ).first()
        if plan:
            typer.echo(f"Planner:  {plan.planner}")
            typer.echo(f"Objective: {plan.objective[:200]}")
        for task in session.scalars(
            select(Task).where(Task.goal_id == goal_id).order_by(Task.created_at)
        ):
            typer.echo(
                f"  {task.id}  {task.status:<11} {task.worker or '-':<12} "
                f"attempts={task.attempt_count} review={task.review_verdict or '-'} "
                f"{task.title[:50]}"
            )


@goal_app.command("cancel")
def goal_cancel(goal_id: str) -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import Goal, Task
    from nexus.domain.enums import GoalStatus, TaskStatus
    from nexus.domain.transitions import InvalidTransition, assert_goal_transition

    with session_scope() as session:
        goal = session.get(Goal, goal_id)
        if goal is None:
            typer.echo("goal not found")
            raise typer.Exit(1)
        try:
            goal.status = assert_goal_transition(GoalStatus(goal.status), GoalStatus.CANCELLED)
        except InvalidTransition:
            typer.echo(f"cannot cancel a goal in status {goal.status}")
            raise typer.Exit(1) from None
        for task in session.scalars(select(Task).where(Task.goal_id == goal_id)):
            if TaskStatus(task.status) in {
                TaskStatus.PENDING,
                TaskStatus.READY,
                TaskStatus.QUEUED,
                TaskStatus.BLOCKED,
            }:
                task.status = TaskStatus.CANCELLED
        typer.echo(f"Goal {goal_id} cancelled.")


@goal_app.command("approve-plan")
def goal_approve_plan(goal_id: str) -> None:
    """Approve a manual-autonomy goal's plan so its tasks can execute."""
    from nexus.db.base import session_scope
    from nexus.db.models import Approval
    from nexus.services.approvals import decide_approval

    with session_scope() as session:
        approval = session.scalars(
            select(Approval)
            .where(
                Approval.goal_id == goal_id,
                Approval.kind == "approve-plan",
                Approval.state == "pending",
            )
            .order_by(Approval.created_at.desc())
        ).first()
        if approval is None:
            typer.echo("no pending plan approval for this goal")
            raise typer.Exit(1)
        decide_approval(session, approval.id, "approved")
        typer.echo("Plan approved; tasks will be enqueued.")


@goal_app.command("retry")
def goal_retry(goal_id: str) -> None:
    """Requeue the failed tasks of a failed goal (one extra attempt each)."""
    from nexus.db.base import session_scope
    from nexus.db.models import Goal, Task
    from nexus.domain.enums import GoalStatus, TaskStatus
    from nexus.domain.transitions import assert_goal_transition, assert_task_transition

    with session_scope() as session:
        goal = session.get(Goal, goal_id)
        if goal is None or GoalStatus(goal.status) != GoalStatus.FAILED:
            typer.echo("goal not found or not in failed state")
            raise typer.Exit(1)
        retried = 0
        for task in session.scalars(
            select(Task).where(Task.goal_id == goal_id, Task.status == TaskStatus.FAILED)
        ):
            task.status = assert_task_transition(TaskStatus.FAILED, TaskStatus.QUEUED)
            task.max_attempts = task.attempt_count + 1
            retried += 1
        goal.status = assert_goal_transition(GoalStatus.FAILED, GoalStatus.PLANNING)
        goal.status = assert_goal_transition(GoalStatus.PLANNING, GoalStatus.READY)
        typer.echo(f"Requeued {retried} failed task(s); goal is ready again.")


# --- task extensions ---------------------------------------------------------------
@task_app.command("inspect")
def task_inspect(task_id: str) -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import ReviewFinding, Task

    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            typer.echo("task not found")
            raise typer.Exit(1)
        typer.echo(f"Task:    {task.title}")
        typer.echo(f"Status:  {task.status}  kind={task.kind}  risk={task.risk}")
        typer.echo(
            f"Worker:  {task.worker or '-'}  reviewer={task.reviewer or '-'}  "
            f"verdict={task.review_verdict or '-'}"
        )
        typer.echo(f"Attempts: {task.attempt_count}/{task.max_attempts}")
        typer.echo(f"Branch:  {task.branch or '-'}")
        typer.echo(f"Worktree: {task.worktree_path or '-'}")
        findings = session.scalars(
            select(ReviewFinding).where(ReviewFinding.task_id == task_id)
        ).all()
        for finding in findings:
            marker = "BLOCKING" if finding.blocking else "note"
            typer.echo(f"  [{finding.severity}/{marker}] {finding.description[:100]}")


@task_app.command("retry")
def task_retry(task_id: str) -> None:
    """Explicit operator retry of a failed task (grants one more attempt)."""
    from nexus.db.base import session_scope
    from nexus.db.models import Task
    from nexus.domain.enums import TaskStatus
    from nexus.domain.transitions import assert_task_transition
    from nexus.services.events import record_audit

    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None or TaskStatus(task.status) != TaskStatus.FAILED:
            typer.echo("task not found or not in failed state")
            raise typer.Exit(1)
        task.status = assert_task_transition(TaskStatus.FAILED, TaskStatus.QUEUED)
        task.max_attempts = task.attempt_count + 1
        record_audit(session, "task.operator-retry", task_id=task_id, actor="owner-cli")
        typer.echo("Task requeued with one additional attempt.")


@task_app.command("cancel")
def task_cancel(task_id: str) -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import Task
    from nexus.domain.enums import TaskStatus
    from nexus.domain.transitions import InvalidTransition, assert_task_transition

    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            typer.echo("task not found")
            raise typer.Exit(1)
        try:
            task.status = assert_task_transition(TaskStatus(task.status), TaskStatus.CANCELLED)
        except InvalidTransition:
            typer.echo(f"cannot cancel a task in status {task.status}")
            raise typer.Exit(1) from None
        typer.echo("Task cancelled.")


# --- worker extensions -----------------------------------------------------------
@worker_app.command("doctor")
def worker_doctor(
    install_codex: bool = typer.Option(
        False,
        "--install-codex",
        help="Install the official Codex CLI via npm if it is missing.",
    ),
) -> None:
    """Health-check all workers; optionally install the missing Codex CLI."""
    import shutil as shutil_module

    from nexus.workers.registry import get_registry

    if install_codex and shutil_module.which("codex") is None:
        from nexus.execution import get_profile, get_runner

        typer.echo("Installing @openai/codex via npm (official interface)...")
        result = get_runner().run(
            get_profile("tool-install"),
            ["npm", "install", "-g", "@openai/codex"],
            cwd=Path.home(),
            timeout=600,
        )
        if not result.ok:
            typer.echo(f"Install failed: {(result.stderr or result.error or '')[:400]}")
            raise typer.Exit(1)
        typer.echo(
            "Installed. Complete login with `codex login` (browser opens; "
            "use the ChatGPT Plus account)."
        )
    for name, health in get_registry().all_health().items():
        state = "available" if health.available else "unavailable"
        typer.echo(
            f"{str(name):<12} {state:<12} version={health.version or '-'} "
            f"auth={health.authenticated} {health.detail}"
        )


@worker_app.command("enable")
def worker_enable(name: str) -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import Worker

    with session_scope() as session:
        worker = session.scalars(select(Worker).where(Worker.name == name)).first()
        if worker is None:
            typer.echo("worker not found (run `nexus bootstrap`)")
            raise typer.Exit(1)
        worker.enabled = True
        typer.echo(f"{name} enabled.")


@worker_app.command("disable")
def worker_disable(name: str) -> None:
    from nexus.db.base import session_scope
    from nexus.db.models import Worker

    with session_scope() as session:
        worker = session.scalars(select(Worker).where(Worker.name == name)).first()
        if worker is None:
            typer.echo("worker not found (run `nexus bootstrap`)")
            raise typer.Exit(1)
        worker.enabled = False
        typer.echo(f"{name} disabled. The router will not select it.")


@worker_app.command("route-explain")
def worker_route_explain(
    kind: str = typer.Argument(
        "implementation", help="Task kind, e.g. implementation, review, planning"
    ),
) -> None:
    """Explain which worker the router would select for a task kind and why."""
    from nexus.domain.enums import TaskKind
    from nexus.routing.router import DEFAULT_PREFERENCES, NoWorkerAvailable, Router
    from nexus.workers.registry import get_registry

    try:
        task_kind = TaskKind(kind)
    except ValueError:
        typer.echo(f"unknown task kind: {kind}. Valid: {', '.join(TaskKind)}")
        raise typer.Exit(1) from None
    available = get_registry().available_names()
    typer.echo(f"Available workers: {', '.join(sorted(str(w) for w in available)) or 'none'}")
    preference = DEFAULT_PREFERENCES.get(task_kind)
    if preference:
        typer.echo(f"Preference order for {kind}: {' > '.join(str(w) for w in preference)}")
    try:
        decision = Router(available=available).route(task_kind)
    except NoWorkerAvailable as exc:
        typer.echo(f"Routing would fail: {exc}")
        raise typer.Exit(1) from None
    typer.echo(f"Selected worker:  {decision.worker} ({decision.reason})")
    typer.echo(
        f"Reviewer:         {
            decision.reviewer or 'none available (single-worker fallback per review policy)'
        }"
    )


# --- github ------------------------------------------------------------------------
@github_app.command("status")
def github_status() -> None:
    from nexus.adapters.github import GitHubAdapter

    adapter = GitHubAdapter()
    auth = adapter.auth_status()
    typer.echo(f"gh authenticated: {auth['authenticated']}")
    from nexus.db.base import session_scope
    from nexus.db.models import PullRequestRecord

    with session_scope() as session:
        for record in session.scalars(
            select(PullRequestRecord).order_by(PullRequestRecord.created_at.desc()).limit(10)
        ):
            typer.echo(
                f"PR #{record.number}  {record.state:<8} {record.branch:<40} {record.url or ''}"
            )


@github_app.command("labels-bootstrap")
def github_labels_bootstrap(repo: str = typer.Option(None, "--repo")) -> None:
    from nexus.adapters.github import GitHubAdapter, GitHubError

    try:
        created = GitHubAdapter(repo=repo).ensure_labels()
    except GitHubError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None
    typer.echo(f"Labels created: {', '.join(created) or '(all already present)'}")


feedback_app = typer.Typer(help="PR feedback intake.", no_args_is_help=True)
github_app.add_typer(feedback_app, name="feedback")


@feedback_app.command("import")
def github_feedback_import(goal_id: str = typer.Argument(...)) -> None:
    """Import new actionable PR comments for a goal as repair tasks."""
    from nexus.db.base import session_scope
    from nexus.services.github_feedback import import_pr_feedback

    try:
        with session_scope() as session:
            report = import_pr_feedback(session, goal_id)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None
    typer.echo(
        f"Fetched {report.fetched} comment(s): {report.actionable} actionable, "
        f"{report.ignored} ignored, {report.duplicates} already processed."
    )
    for task_id in report.repair_tasks:
        typer.echo(f"  repair task created: {task_id}")


@github_app.command("sync")
def github_sync(goal_id: str = typer.Argument(...)) -> None:
    """Re-deliver a goal: push its branch and update its draft PR."""
    from nexus.db.base import session_scope
    from nexus.db.models import Goal
    from nexus.services.delivery import deliver_goal

    with session_scope() as session:
        goal = session.get(Goal, goal_id)
        if goal is None:
            typer.echo("goal not found")
            raise typer.Exit(1)
        record = deliver_goal(session, goal)
        if record is None:
            typer.echo("nothing to deliver (no repository, remote, or completed branch)")
            raise typer.Exit(1)
        typer.echo(f"Delivered: PR #{record.number} {record.url}")


def main() -> None:  # console-script shim used by some packagers
    app()


if __name__ == "__main__":
    app()
