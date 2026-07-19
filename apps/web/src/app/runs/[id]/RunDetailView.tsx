"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { Card } from "@/components/Card";
import { ConfirmActionButton } from "@/components/ConfirmActionButton";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { EmptyState } from "@/components/EmptyState";
import { PollingIndicator } from "@/components/PollingIndicator";
import { StatusBadge } from "@/components/StatusBadge";
import { formatDateTime, formatDuration, formatTime } from "@/lib/format";

export function RunDetailView({ id }: { id: string }) {
  const run = usePolling(() => api.getRun(id), 5000, [id]);

  const cancellable =
    run.data?.status === "queued" || run.data?.status === "running";

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Link
            href="/runs"
            className="text-xs text-zinc-500 hover:text-zinc-300"
          >
            Runs
          </Link>
          <h1 className="mt-1 truncate text-lg font-semibold text-zinc-100">
            {run.data?.task_title ?? "Run"}
          </h1>
          <p className="mt-0.5 font-mono text-xs text-zinc-500">{id}</p>
        </div>
        <div className="flex shrink-0 items-center gap-4">
          <PollingIndicator lastUpdated={run.lastUpdated} error={run.error} />
          {cancellable ? (
            <ConfirmActionButton
              label="Cancel run"
              confirmLabel="Confirm cancel"
              onConfirm={async () => {
                await api.cancelRun(id);
                run.refresh();
              }}
            />
          ) : null}
        </div>
      </div>

      <ConnectionBanner visible={run.unreachable} />

      {run.error && !run.unreachable && !run.data ? (
        <div
          role="alert"
          className="rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300"
        >
          {run.error}
        </div>
      ) : null}

      {run.data ? (
        <>
          <Card title="Run details">
            <dl className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Status</dt>
                <dd className="mt-0.5">
                  <StatusBadge status={run.data.status} />
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Worker</dt>
                <dd className="mt-0.5 text-zinc-300">{run.data.worker}</dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Duration</dt>
                <dd className="mt-0.5 text-zinc-300">
                  {formatDuration(run.data.started_at, run.data.finished_at)}
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Started</dt>
                <dd className="mt-0.5 text-zinc-300">
                  {formatDateTime(run.data.started_at)}
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Finished</dt>
                <dd className="mt-0.5 text-zinc-300">
                  {formatDateTime(run.data.finished_at)}
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Task ID</dt>
                <dd className="mt-0.5 font-mono text-xs text-zinc-400">
                  {run.data.task_id}
                </dd>
              </div>
            </dl>
            {run.data.exit_summary ? (
              <p className="mt-4 rounded-md border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-300">
                {run.data.exit_summary}
              </p>
            ) : null}
          </Card>

          <Card title={`Events (${run.data.events.length})`}>
            {run.data.events.length > 0 ? (
              <ol className="max-h-[32rem] space-y-0.5 overflow-y-auto font-mono text-xs leading-relaxed">
                {run.data.events.map((event, index) => (
                  <li
                    key={`${event.ts}-${index}`}
                    className="flex gap-3 rounded px-2 py-1 hover:bg-zinc-800/40"
                  >
                    <span className="shrink-0 text-zinc-600">
                      {formatTime(event.ts)}
                    </span>
                    <span className="shrink-0 text-sky-400/80">
                      {event.type}
                    </span>
                    {event.status ? (
                      <span className="shrink-0 text-zinc-500">
                        [{event.status}]
                      </span>
                    ) : null}
                    <span className="break-all whitespace-pre-wrap text-zinc-300">
                      {event.message}
                    </span>
                  </li>
                ))}
              </ol>
            ) : (
              <EmptyState message="No events recorded for this run yet." />
            )}
          </Card>
        </>
      ) : !run.unreachable && run.loading ? (
        <Card>
          <p className="text-sm text-zinc-500">Loading run…</p>
        </Card>
      ) : null}
    </div>
  );
}
