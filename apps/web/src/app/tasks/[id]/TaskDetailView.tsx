"use client";

import { useState } from "react";
import Link from "next/link";
import { api, type Finding } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { Card } from "@/components/Card";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { EmptyState } from "@/components/EmptyState";
import { PollingIndicator } from "@/components/PollingIndicator";
import { StatusBadge } from "@/components/StatusBadge";
import { formatDateTime, formatMs } from "@/lib/format";

function RetryButton({
  taskId,
  onRetried,
}: {
  taskId: string;
  onRetried: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const retry = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.retryTask(taskId);
      onRetried();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="inline-flex items-center gap-2">
      {error ? <span className="text-xs text-red-400">{error}</span> : null}
      <button
        type="button"
        disabled={busy}
        onClick={() => void retry()}
        className="rounded-md border border-sky-500/40 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-300 hover:bg-sky-500/20 disabled:opacity-50"
      >
        {busy ? "Retrying…" : "Retry task"}
      </button>
    </span>
  );
}

function FindingItem({ finding }: { finding: Finding }) {
  return (
    <li className="rounded-md border border-zinc-800 bg-zinc-950/60 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={finding.severity} />
        {finding.blocking ? (
          <span className="inline-flex items-center rounded-full border border-red-500/40 bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-300">
            blocking
          </span>
        ) : null}
        {finding.resolved ? (
          <span className="inline-flex items-center rounded-full border border-emerald-500/25 bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-400">
            resolved
          </span>
        ) : null}
        <span className="text-xs text-zinc-500">{finding.category}</span>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-zinc-300">
        {finding.description}
      </p>
      {finding.file ? (
        <p className="mt-1.5 font-mono text-xs text-zinc-500">
          {finding.file}
          {finding.line ? `:${finding.line}` : ""}
        </p>
      ) : null}
      {finding.recommendation ? (
        <p className="mt-2 rounded-md border border-zinc-800 bg-zinc-900/80 px-3 py-2 text-xs leading-relaxed text-zinc-400">
          {finding.recommendation}
        </p>
      ) : null}
      <p className="mt-2 text-xs text-zinc-600">
        {finding.source} · reviewed by {finding.reviewer}
      </p>
    </li>
  );
}

export function TaskDetailView({ id }: { id: string }) {
  const task = usePolling(() => api.getTask(id), 5000, [id]);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          {task.data?.goal_id ? (
            <Link
              href={`/goals/${task.data.goal_id}`}
              className="text-xs text-zinc-500 hover:text-zinc-300"
            >
              Goal
            </Link>
          ) : (
            <Link
              href="/goals"
              className="text-xs text-zinc-500 hover:text-zinc-300"
            >
              Goals
            </Link>
          )}
          <h1 className="mt-1 truncate text-lg font-semibold text-zinc-100">
            {task.data?.title ?? "Task"}
          </h1>
          <p className="mt-0.5 font-mono text-xs text-zinc-500">{id}</p>
        </div>
        <div className="flex shrink-0 items-center gap-4">
          <PollingIndicator lastUpdated={task.lastUpdated} error={task.error} />
          {task.data?.status === "failed" ? (
            <RetryButton taskId={id} onRetried={task.refresh} />
          ) : null}
        </div>
      </div>

      <ConnectionBanner visible={task.unreachable} />

      {task.error && !task.unreachable && !task.data ? (
        <div
          role="alert"
          className="rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300"
        >
          {task.error}
        </div>
      ) : null}

      {task.data ? (
        <>
          <Card title="Task details">
            <dl className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Status</dt>
                <dd className="mt-0.5">
                  <StatusBadge status={task.data.status} />
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Worker</dt>
                <dd className="mt-0.5 text-zinc-300">
                  {task.data.worker ?? "—"}
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Reviewer</dt>
                <dd className="mt-0.5 text-zinc-300">
                  {task.data.reviewer ?? "—"}
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Review verdict</dt>
                <dd className="mt-0.5">
                  {task.data.review_verdict ? (
                    <StatusBadge status={task.data.review_verdict} />
                  ) : (
                    <span className="text-zinc-300">—</span>
                  )}
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Risk</dt>
                <dd className="mt-0.5">
                  <StatusBadge status={task.data.risk} />
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Attempts</dt>
                <dd className="mt-0.5 text-zinc-300">
                  {task.data.attempt_count}
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Branch</dt>
                <dd className="mt-0.5 font-mono text-xs text-zinc-400">
                  {task.data.branch ?? "—"}
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Worktree</dt>
                <dd className="mt-0.5 font-mono text-xs break-all text-zinc-400">
                  {task.data.worktree_path ?? "—"}
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-zinc-500">Created</dt>
                <dd className="mt-0.5 text-zinc-300">
                  {formatDateTime(task.data.created_at)}
                </dd>
              </div>
            </dl>
          </Card>

          <Card title="Instruction">
            {task.data.instruction ? (
              <details>
                <summary className="cursor-pointer text-sm text-zinc-400 hover:text-zinc-200">
                  Show worker instruction
                </summary>
                <pre className="mt-3 max-h-[28rem] overflow-auto rounded-md border border-zinc-800 bg-zinc-950/80 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-zinc-300">
                  {task.data.instruction}
                </pre>
              </details>
            ) : (
              <EmptyState message="No instruction recorded for this task." />
            )}
          </Card>

          <Card
            title={`Validation results (${task.data.validation_results.length})`}
          >
            {task.data.validation_results.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-zinc-800 text-xs text-zinc-500">
                      <th className="py-2 pr-4 font-medium">Kind</th>
                      <th className="py-2 pr-4 font-medium">Status</th>
                      <th className="py-2 pr-4 font-medium">Summary</th>
                      <th className="py-2 font-medium">Duration</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/70">
                    {task.data.validation_results.map((result, index) => (
                      <tr key={`${result.kind}-${index}`}>
                        <td className="py-2.5 pr-4 font-mono text-xs text-zinc-300">
                          {result.kind}
                        </td>
                        <td className="py-2.5 pr-4">
                          <StatusBadge status={result.status} />
                        </td>
                        <td className="max-w-md py-2.5 pr-4 text-zinc-400">
                          {result.summary || "—"}
                        </td>
                        <td className="py-2.5 text-zinc-400">
                          {formatMs(result.duration_ms)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState message="No validation results yet." />
            )}
          </Card>

          <Card title={`Findings (${task.data.findings.length})`}>
            {task.data.findings.length > 0 ? (
              <ul className="space-y-3">
                {task.data.findings.map((finding) => (
                  <FindingItem key={finding.id} finding={finding} />
                ))}
              </ul>
            ) : (
              <EmptyState message="No review findings for this task." />
            )}
          </Card>
        </>
      ) : !task.unreachable && task.loading ? (
        <Card>
          <p className="text-sm text-zinc-500">Loading task…</p>
        </Card>
      ) : null}
    </div>
  );
}
