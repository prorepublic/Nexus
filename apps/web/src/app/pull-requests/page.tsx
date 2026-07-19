"use client";

import { useState } from "react";
import Link from "next/link";
import { api, type FeedbackImportResult, type PullRequest } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { Card } from "@/components/Card";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { EmptyState } from "@/components/EmptyState";
import { PollingIndicator } from "@/components/PollingIndicator";
import { StatusBadge } from "@/components/StatusBadge";
import { formatRelative, shortId } from "@/lib/format";

function summarizeImport(result: FeedbackImportResult): string {
  const parts = [
    `${result.actionable} actionable → ${result.repair_tasks.length} repair task${
      result.repair_tasks.length === 1 ? "" : "s"
    }`,
  ];
  if (result.ignored > 0) parts.push(`${result.ignored} ignored`);
  if (result.duplicates > 0) parts.push(`${result.duplicates} duplicates`);
  return `Fetched ${result.fetched}: ${parts.join(", ")}`;
}

function ImportFeedbackButton({ pullRequest }: { pullRequest: PullRequest }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<FeedbackImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const importFeedback = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.importGoalFeedback(pullRequest.goal_id));
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <button
        type="button"
        disabled={busy}
        onClick={() => void importFeedback()}
        aria-label={`Import review feedback for pull request on ${pullRequest.branch}`}
        className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
      >
        {busy ? "Importing…" : "Import feedback"}
      </button>
      {result ? (
        <p className="mt-1 text-xs text-emerald-400">
          {summarizeImport(result)}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-1 text-xs text-red-400">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export default function PullRequestsPage() {
  const prs = usePolling(api.listPullRequests, 5000);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">
            Pull Requests
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Pull requests opened by goals. Import review feedback to spawn
            repair tasks for actionable comments.
          </p>
        </div>
        <PollingIndicator lastUpdated={prs.lastUpdated} error={prs.error} />
      </div>

      <ConnectionBanner visible={prs.unreachable} />

      <Card>
        {prs.data ? (
          prs.data.items.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-zinc-800 text-xs text-zinc-500">
                    <th className="py-2 pr-4 font-medium">Repository</th>
                    <th className="py-2 pr-4 font-medium">Number</th>
                    <th className="py-2 pr-4 font-medium">Branch</th>
                    <th className="py-2 pr-4 font-medium">State</th>
                    <th className="py-2 pr-4 font-medium">Goal</th>
                    <th className="py-2 pr-4 font-medium">Updated</th>
                    <th className="py-2 font-medium">Feedback</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/70">
                  {prs.data.items.map((pr) => (
                    <tr key={pr.id}>
                      <td className="py-2.5 pr-4 text-zinc-300">
                        {pr.repository}
                      </td>
                      <td className="py-2.5 pr-4">
                        {pr.number !== null ? (
                          pr.url ? (
                            <a
                              href={pr.url}
                              target="_blank"
                              rel="noreferrer"
                              className="font-mono text-sky-400 hover:text-sky-300"
                            >
                              #{pr.number}
                            </a>
                          ) : (
                            <span className="font-mono text-zinc-400">
                              #{pr.number}
                            </span>
                          )
                        ) : (
                          <span className="text-zinc-600">—</span>
                        )}
                      </td>
                      <td className="py-2.5 pr-4 font-mono text-xs text-zinc-400">
                        {pr.branch}
                      </td>
                      <td className="py-2.5 pr-4">
                        <StatusBadge status={pr.state} />
                      </td>
                      <td className="py-2.5 pr-4">
                        <Link
                          href={`/goals/${pr.goal_id}`}
                          className="font-mono text-xs text-sky-400 hover:text-sky-300"
                        >
                          {shortId(pr.goal_id)}
                        </Link>
                      </td>
                      <td className="py-2.5 pr-4 text-xs text-zinc-500">
                        {formatRelative(pr.updated_at)}
                      </td>
                      <td className="py-2.5">
                        <ImportFeedbackButton pullRequest={pr} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState message="No pull requests yet. They appear when goals push branches and open PRs." />
          )
        ) : (
          <p className="text-sm text-zinc-500">
            {prs.unreachable ? "Unavailable." : "Loading…"}
          </p>
        )}
      </Card>
    </div>
  );
}
