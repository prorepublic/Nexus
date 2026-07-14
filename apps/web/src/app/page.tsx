"use client";

import Link from "next/link";
import { api, type Run, type Goal } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { Card } from "@/components/Card";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { EmptyState } from "@/components/EmptyState";
import { PollingIndicator } from "@/components/PollingIndicator";
import { StatusBadge } from "@/components/StatusBadge";
import { formatRelative } from "@/lib/format";

const ACTIVE_RUN_STATUSES = new Set<Run["status"]>(["queued", "running"]);
const ATTENTION_GOAL_STATUSES = new Set<Goal["status"]>(["failed", "blocked"]);

export default function DashboardPage() {
  const system = usePolling(api.systemStatus, 5000);
  const goals = usePolling(() => api.listGoals(20), 5000);
  const runs = usePolling(() => api.listRuns(50), 5000);
  const approvals = usePolling(() => api.listApprovals("pending"), 5000);

  const unreachable =
    system.unreachable &&
    goals.unreachable &&
    runs.unreachable &&
    approvals.unreachable;

  const activeRuns =
    runs.data?.items.filter((run) => ACTIVE_RUN_STATUSES.has(run.status)) ??
    [];
  const failedRuns =
    runs.data?.items.filter(
      (run) => run.status === "failed" || run.status === "timed_out",
    ) ?? [];
  const attentionGoals =
    goals.data?.items.filter((goal) =>
      ATTENTION_GOAL_STATUSES.has(goal.status),
    ) ?? [];
  const pendingCount = approvals.data?.items.length ?? null;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-lg font-semibold text-zinc-100">Dashboard</h1>
        <PollingIndicator
          lastUpdated={system.lastUpdated}
          error={system.error}
        />
      </div>

      <ConnectionBanner visible={unreachable} />

      <div className="grid gap-4 md:grid-cols-3">
        <Card title="System health">
          {system.data ? (
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-zinc-500">API</dt>
                <dd>
                  <StatusBadge status={system.unreachable ? "down" : "ok"} />
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-zinc-500">Database</dt>
                <dd>
                  <StatusBadge
                    status={
                      system.data.database === "ok" ? "ok" : "unavailable"
                    }
                  />
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-zinc-500">Version</dt>
                <dd className="font-mono text-zinc-300">
                  {system.data.version}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="text-sm text-zinc-500">
              {system.unreachable ? "Control plane unreachable." : "Loading…"}
            </p>
          )}
        </Card>

        <Card title="Pending approvals">
          <div className="flex items-center justify-between">
            <span className="text-3xl font-semibold text-zinc-100">
              {pendingCount ?? "—"}
            </span>
            <Link
              href="/approvals"
              className="text-sm text-sky-400 hover:text-sky-300"
            >
              Review
            </Link>
          </div>
          <p className="mt-2 text-xs text-zinc-500">
            Actions waiting for an operator decision.
          </p>
        </Card>

        <Card title="Cost mode">
          <p className="text-sm text-amber-300">
            Cost mode: subscription CLIs only — paid APIs disabled
          </p>
          <p className="mt-2 text-xs text-zinc-500">
            {system.data?.cost_mode.description ??
              "Workers run through locally installed subscription CLIs."}
          </p>
        </Card>
      </div>

      <Card title="Workers">
        {system.data ? (
          system.data.workers.length > 0 ? (
            <div className="grid gap-3 md:grid-cols-3">
              {system.data.workers.map((worker) => (
                <div
                  key={worker.name}
                  className="rounded-md border border-zinc-800 bg-zinc-950/60 p-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium text-zinc-200">
                      {worker.name}
                    </span>
                    <StatusBadge
                      status={worker.available ? "available" : "unavailable"}
                    />
                  </div>
                  <dl className="mt-2 space-y-1 text-xs text-zinc-500">
                    <div className="flex justify-between">
                      <dt>Provider</dt>
                      <dd className="text-zinc-400">{worker.provider}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt>Version</dt>
                      <dd className="font-mono text-zinc-400">
                        {worker.version ?? "not installed"}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt>Authenticated</dt>
                      <dd className="text-zinc-400">
                        {worker.authenticated === null
                          ? "n/a"
                          : worker.authenticated
                            ? "yes"
                            : "no"}
                      </dd>
                    </div>
                  </dl>
                  {worker.detail ? (
                    <p className="mt-2 text-xs text-zinc-600">
                      {worker.detail}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <EmptyState message="No workers registered." />
          )
        ) : (
          <p className="text-sm text-zinc-500">
            {system.unreachable
              ? "Worker status unavailable while the control plane is down."
              : "Loading…"}
          </p>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="Recent goals"
          action={
            <Link
              href="/goals"
              className="text-xs text-sky-400 hover:text-sky-300"
            >
              View all
            </Link>
          }
        >
          {goals.data ? (
            goals.data.items.length > 0 ? (
              <ul className="divide-y divide-zinc-800/70">
                {goals.data.items.slice(0, 8).map((goal) => (
                  <li key={goal.id}>
                    <Link
                      href={`/goals/${goal.id}`}
                      className="flex items-center justify-between gap-3 py-2.5 hover:bg-zinc-800/30"
                    >
                      <span className="truncate text-sm text-zinc-300">
                        {goal.title}
                      </span>
                      <span className="flex shrink-0 items-center gap-2">
                        <span className="text-xs text-zinc-600">
                          {formatRelative(goal.updated_at)}
                        </span>
                        <StatusBadge status={goal.status} />
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState
                message="No goals yet."
                action={
                  <Link
                    href="/goals/new"
                    className="text-sm text-sky-400 hover:text-sky-300"
                  >
                    Create the first goal
                  </Link>
                }
              />
            )
          ) : (
            <p className="text-sm text-zinc-500">
              {goals.unreachable ? "Unavailable." : "Loading…"}
            </p>
          )}
        </Card>

        <Card
          title="Active runs"
          action={
            <Link
              href="/runs"
              className="text-xs text-sky-400 hover:text-sky-300"
            >
              View all
            </Link>
          }
        >
          {runs.data ? (
            activeRuns.length > 0 ? (
              <ul className="divide-y divide-zinc-800/70">
                {activeRuns.slice(0, 8).map((run) => (
                  <li key={run.id}>
                    <Link
                      href={`/runs/${run.id}`}
                      className="flex items-center justify-between gap-3 py-2.5 hover:bg-zinc-800/30"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-sm text-zinc-300">
                          {run.task_title}
                        </span>
                        <span className="text-xs text-zinc-600">
                          {run.worker}
                        </span>
                      </span>
                      <StatusBadge status={run.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState message="No queued or running work right now." />
            )
          ) : (
            <p className="text-sm text-zinc-500">
              {runs.unreachable ? "Unavailable." : "Loading…"}
            </p>
          )}
        </Card>
      </div>

      <Card title="Needs attention">
        {attentionGoals.length === 0 && failedRuns.length === 0 ? (
          <EmptyState message="Nothing failed or blocked." />
        ) : (
          <div className="space-y-4">
            {attentionGoals.length > 0 ? (
              <div>
                <h3 className="mb-2 text-xs font-medium tracking-wide text-zinc-500 uppercase">
                  Goals
                </h3>
                <ul className="divide-y divide-zinc-800/70">
                  {attentionGoals.map((goal) => (
                    <li key={goal.id}>
                      <Link
                        href={`/goals/${goal.id}`}
                        className="flex items-center justify-between gap-3 py-2 hover:bg-zinc-800/30"
                      >
                        <span className="truncate text-sm text-zinc-300">
                          {goal.title}
                        </span>
                        <StatusBadge status={goal.status} />
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {failedRuns.length > 0 ? (
              <div>
                <h3 className="mb-2 text-xs font-medium tracking-wide text-zinc-500 uppercase">
                  Runs
                </h3>
                <ul className="divide-y divide-zinc-800/70">
                  {failedRuns.slice(0, 8).map((run) => (
                    <li key={run.id}>
                      <Link
                        href={`/runs/${run.id}`}
                        className="flex items-center justify-between gap-3 py-2 hover:bg-zinc-800/30"
                      >
                        <span className="min-w-0">
                          <span className="block truncate text-sm text-zinc-300">
                            {run.task_title}
                          </span>
                          {run.exit_summary ? (
                            <span className="block truncate text-xs text-zinc-600">
                              {run.exit_summary}
                            </span>
                          ) : null}
                        </span>
                        <StatusBadge status={run.status} />
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
      </Card>
    </div>
  );
}
