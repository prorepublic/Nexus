"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { Card } from "@/components/Card";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { EmptyState } from "@/components/EmptyState";
import { PollingIndicator } from "@/components/PollingIndicator";
import { StatusBadge } from "@/components/StatusBadge";
import { formatDuration, formatRelative, shortId } from "@/lib/format";

export default function RunsPage() {
  const runs = usePolling(() => api.listRuns(50), 5000);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-lg font-semibold text-zinc-100">Runs</h1>
        <PollingIndicator lastUpdated={runs.lastUpdated} error={runs.error} />
      </div>

      <ConnectionBanner visible={runs.unreachable} />

      <Card>
        {runs.data ? (
          runs.data.items.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-zinc-800 text-xs text-zinc-500">
                    <th className="py-2 pr-4 font-medium">Run</th>
                    <th className="py-2 pr-4 font-medium">Task</th>
                    <th className="py-2 pr-4 font-medium">Worker</th>
                    <th className="py-2 pr-4 font-medium">Status</th>
                    <th className="py-2 pr-4 font-medium">Started</th>
                    <th className="py-2 font-medium">Duration</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/70">
                  {runs.data.items.map((run) => (
                    <tr key={run.id} className="hover:bg-zinc-800/30">
                      <td className="py-2.5 pr-4">
                        <Link
                          href={`/runs/${run.id}`}
                          className="font-mono text-xs text-sky-400 hover:text-sky-300"
                        >
                          {shortId(run.id)}
                        </Link>
                      </td>
                      <td className="max-w-xs py-2.5 pr-4">
                        <Link
                          href={`/runs/${run.id}`}
                          className="block truncate text-zinc-300 hover:text-zinc-100"
                        >
                          {run.task_title}
                        </Link>
                      </td>
                      <td className="py-2.5 pr-4 text-zinc-400">
                        {run.worker}
                      </td>
                      <td className="py-2.5 pr-4">
                        <StatusBadge status={run.status} />
                      </td>
                      <td className="py-2.5 pr-4 whitespace-nowrap text-zinc-400">
                        {formatRelative(run.started_at)}
                      </td>
                      <td className="py-2.5 whitespace-nowrap text-zinc-400">
                        {formatDuration(run.started_at, run.finished_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState message="No runs recorded yet." />
          )
        ) : (
          <p className="text-sm text-zinc-500">
            {runs.unreachable ? "Unavailable." : "Loading…"}
          </p>
        )}
      </Card>
    </div>
  );
}
