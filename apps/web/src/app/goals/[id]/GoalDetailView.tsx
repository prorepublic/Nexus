"use client";

import Link from "next/link";
import { api, type GoalStatus } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { Card } from "@/components/Card";
import { ConfirmActionButton } from "@/components/ConfirmActionButton";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { EmptyState } from "@/components/EmptyState";
import { PollingIndicator } from "@/components/PollingIndicator";
import { StatusBadge } from "@/components/StatusBadge";
import { formatDateTime } from "@/lib/format";

const CANCELLABLE_STATUSES = new Set<GoalStatus>([
  "draft",
  "planning",
  "ready",
  "executing",
  "blocked",
  "review",
]);

export function GoalDetailView({ id }: { id: string }) {
  const goal = usePolling(() => api.getGoal(id), 5000, [id]);

  const cancellable =
    goal.data !== null && CANCELLABLE_STATUSES.has(goal.data.status);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Link
            href="/goals"
            className="text-xs text-zinc-500 hover:text-zinc-300"
          >
            Goals
          </Link>
          <h1 className="mt-1 truncate text-lg font-semibold text-zinc-100">
            {goal.data?.title ?? "Goal"}
          </h1>
        </div>
        <div className="flex shrink-0 items-center gap-4">
          <PollingIndicator lastUpdated={goal.lastUpdated} error={goal.error} />
          {cancellable ? (
            <ConfirmActionButton
              label="Cancel goal"
              confirmLabel="Confirm cancel"
              onConfirm={async () => {
                await api.cancelGoal(id);
                goal.refresh();
              }}
            />
          ) : null}
        </div>
      </div>

      <ConnectionBanner visible={goal.unreachable} />

      {goal.error && !goal.unreachable && !goal.data ? (
        <div
          role="alert"
          className="rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300"
        >
          {goal.error}
        </div>
      ) : null}

      {goal.data ? (
        <>
          <Card title="Overview">
            <div className="space-y-4">
              <p className="text-sm leading-relaxed whitespace-pre-wrap text-zinc-300">
                {goal.data.description}
              </p>
              <dl className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
                <div className="flex justify-between gap-4 sm:block">
                  <dt className="text-zinc-500">Status</dt>
                  <dd className="mt-0.5">
                    <StatusBadge status={goal.data.status} />
                  </dd>
                </div>
                <div className="flex justify-between gap-4 sm:block">
                  <dt className="text-zinc-500">Priority</dt>
                  <dd className="mt-0.5 text-zinc-300">
                    {goal.data.priority}
                  </dd>
                </div>
                <div className="flex justify-between gap-4 sm:block">
                  <dt className="text-zinc-500">Autonomy</dt>
                  <dd className="mt-0.5 text-zinc-300">
                    {goal.data.autonomy}
                  </dd>
                </div>
                <div className="flex justify-between gap-4 sm:block">
                  <dt className="text-zinc-500">Repository</dt>
                  <dd className="mt-0.5 font-mono text-zinc-300">
                    {goal.data.repository ?? "none"}
                  </dd>
                </div>
                <div className="flex justify-between gap-4 sm:block">
                  <dt className="text-zinc-500">Requested worker</dt>
                  <dd className="mt-0.5 text-zinc-300">
                    {goal.data.requested_worker ?? "automatic"}
                  </dd>
                </div>
                <div className="flex justify-between gap-4 sm:block">
                  <dt className="text-zinc-500">Created</dt>
                  <dd className="mt-0.5 text-zinc-300">
                    {formatDateTime(goal.data.created_at)}
                  </dd>
                </div>
                <div className="flex justify-between gap-4 sm:block">
                  <dt className="text-zinc-500">Updated</dt>
                  <dd className="mt-0.5 text-zinc-300">
                    {formatDateTime(goal.data.updated_at)}
                  </dd>
                </div>
                <div className="flex justify-between gap-4 sm:block">
                  <dt className="text-zinc-500">Goal ID</dt>
                  <dd className="mt-0.5 font-mono text-xs text-zinc-400">
                    {goal.data.id}
                  </dd>
                </div>
              </dl>
            </div>
          </Card>

          <Card title={`Tasks (${goal.data.tasks.length})`}>
            {goal.data.tasks.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-zinc-800 text-xs text-zinc-500">
                      <th className="py-2 pr-4 font-medium">Task</th>
                      <th className="py-2 pr-4 font-medium">Status</th>
                      <th className="py-2 pr-4 font-medium">Worker</th>
                      <th className="py-2 pr-4 font-medium">Risk</th>
                      <th className="py-2 pr-4 font-medium">Attempts</th>
                      <th className="py-2 font-medium">Branch</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/70">
                    {goal.data.tasks.map((task) => (
                      <tr key={task.id}>
                        <td className="max-w-xs py-2.5 pr-4">
                          <span className="block truncate text-zinc-300">
                            {task.title}
                          </span>
                        </td>
                        <td className="py-2.5 pr-4">
                          <StatusBadge status={task.status} />
                        </td>
                        <td className="py-2.5 pr-4 text-zinc-400">
                          {task.worker ?? "—"}
                        </td>
                        <td className="py-2.5 pr-4">
                          <StatusBadge status={task.risk} />
                        </td>
                        <td className="py-2.5 pr-4 text-zinc-400">
                          {task.attempt_count}
                        </td>
                        <td className="py-2.5 font-mono text-xs text-zinc-400">
                          {task.branch ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState message="No tasks planned for this goal yet." />
            )}
          </Card>
        </>
      ) : !goal.unreachable && goal.loading ? (
        <Card>
          <p className="text-sm text-zinc-500">Loading goal…</p>
        </Card>
      ) : null}
    </div>
  );
}
