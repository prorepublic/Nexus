"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { Card } from "@/components/Card";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { EmptyState } from "@/components/EmptyState";
import { PollingIndicator } from "@/components/PollingIndicator";
import { StatusBadge } from "@/components/StatusBadge";
import { formatRelative } from "@/lib/format";

export default function GoalsPage() {
  const goals = usePolling(() => api.listGoals(20), 5000);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-lg font-semibold text-zinc-100">Goals</h1>
        <div className="flex items-center gap-4">
          <PollingIndicator
            lastUpdated={goals.lastUpdated}
            error={goals.error}
          />
          <Link
            href="/goals/new"
            className="rounded-md bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-white"
          >
            New goal
          </Link>
        </div>
      </div>

      <ConnectionBanner visible={goals.unreachable} />

      <Card>
        {goals.data ? (
          goals.data.items.length > 0 ? (
            <ul className="divide-y divide-zinc-800/70">
              {goals.data.items.map((goal) => (
                <li key={goal.id}>
                  <Link
                    href={`/goals/${goal.id}`}
                    className="flex items-center justify-between gap-4 px-1 py-3 hover:bg-zinc-800/30"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-zinc-200">
                        {goal.title}
                      </span>
                      <span className="mt-0.5 block truncate text-xs text-zinc-500">
                        {goal.description}
                      </span>
                      <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-600">
                        {goal.repository ? (
                          <span className="font-mono">{goal.repository}</span>
                        ) : null}
                        <span>priority: {goal.priority}</span>
                        <span>autonomy: {goal.autonomy}</span>
                        <span>
                          worker: {goal.requested_worker ?? "automatic"}
                        </span>
                        <span>updated {formatRelative(goal.updated_at)}</span>
                      </span>
                    </span>
                    <StatusBadge status={goal.status} />
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
    </div>
  );
}
