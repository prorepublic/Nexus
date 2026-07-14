"use client";

import { useState } from "react";
import Link from "next/link";
import {
  api,
  NexusApiError,
  type GoalPlan,
  type GoalStatus,
} from "@/lib/api";
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

function PlanList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h3 className="mb-1.5 text-xs font-medium tracking-wide text-zinc-500 uppercase">
        {label}
      </h3>
      <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-zinc-300">
        {items.map((item, index) => (
          <li key={index}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function ApprovePlanButton({
  goalId,
  onApproved,
}: {
  goalId: string;
  onApproved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const approve = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.approveGoalPlan(goalId);
      onApproved();
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
        onClick={() => void approve()}
        className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
      >
        {busy ? "Approving…" : "Approve plan"}
      </button>
    </span>
  );
}

export function GoalDetailView({ id }: { id: string }) {
  const goal = usePolling(() => api.getGoal(id), 5000, [id]);
  const plan = usePolling<GoalPlan | null>(
    () =>
      api.getGoalPlan(id).catch((err: unknown) => {
        if (err instanceof NexusApiError && err.status === 404) return null;
        throw err;
      }),
    10000,
    [id],
  );
  const approvals = usePolling(() => api.listApprovals("pending"), 5000);

  const cancellable =
    goal.data !== null && CANCELLABLE_STATUSES.has(goal.data.status);

  const goalTitle = goal.data?.title;
  const planApprovalPending =
    goal.data?.status === "ready" &&
    (approvals.data?.items.some(
      (approval) =>
        approval.kind === "approve-plan" ||
        approval.goal_id === id ||
        approval.description.includes(id) ||
        (goalTitle ? approval.description.includes(goalTitle) : false),
    ) ??
      false);

  const refreshAll = () => {
    goal.refresh();
    plan.refresh();
    approvals.refresh();
  };

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

          {plan.data ? (
            <Card
              title="Plan"
              action={
                planApprovalPending ? (
                  <ApprovePlanButton goalId={id} onApproved={refreshAll} />
                ) : undefined
              }
            >
              <div className="space-y-4">
                <div>
                  <h3 className="mb-1.5 text-xs font-medium tracking-wide text-zinc-500 uppercase">
                    Objective
                  </h3>
                  <p className="text-sm leading-relaxed whitespace-pre-wrap text-zinc-300">
                    {plan.data.objective}
                  </p>
                </div>
                <p className="text-xs text-zinc-500">
                  Planner:{" "}
                  <span className="font-mono text-zinc-400">
                    {plan.data.planner}
                  </span>
                </p>
                <PlanList label="Assumptions" items={plan.data.assumptions} />
                <PlanList label="Risks" items={plan.data.risks} />
                <PlanList
                  label="Validation plan"
                  items={plan.data.validation_plan}
                />
              </div>
            </Card>
          ) : planApprovalPending ? (
            <Card title="Plan">
              <div className="flex items-center justify-between gap-4">
                <p className="text-sm text-zinc-500">
                  This goal is ready and waiting for plan approval.
                </p>
                <ApprovePlanButton goalId={id} onApproved={refreshAll} />
              </div>
            </Card>
          ) : null}

          <Card title={`Tasks (${goal.data.tasks.length})`}>
            {goal.data.tasks.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-zinc-800 text-xs text-zinc-500">
                      <th className="py-2 pr-4 font-medium">Task</th>
                      <th className="py-2 pr-4 font-medium">Status</th>
                      <th className="py-2 pr-4 font-medium">Worker</th>
                      <th className="py-2 pr-4 font-medium">Reviewer</th>
                      <th className="py-2 pr-4 font-medium">Verdict</th>
                      <th className="py-2 pr-4 font-medium">Risk</th>
                      <th className="py-2 pr-4 font-medium">Attempts</th>
                      <th className="py-2 font-medium">Branch</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/70">
                    {goal.data.tasks.map((task) => (
                      <tr key={task.id}>
                        <td className="max-w-xs py-2.5 pr-4">
                          <Link
                            href={`/tasks/${task.id}`}
                            className="block truncate text-zinc-300 hover:text-sky-300"
                          >
                            {task.title}
                          </Link>
                        </td>
                        <td className="py-2.5 pr-4">
                          <StatusBadge status={task.status} />
                        </td>
                        <td className="py-2.5 pr-4 text-zinc-400">
                          {task.worker ?? "—"}
                        </td>
                        <td className="py-2.5 pr-4 text-zinc-400">
                          {task.reviewer ?? "—"}
                        </td>
                        <td className="py-2.5 pr-4">
                          {task.review_verdict ? (
                            <StatusBadge status={task.review_verdict} />
                          ) : (
                            <span className="text-zinc-600">—</span>
                          )}
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
