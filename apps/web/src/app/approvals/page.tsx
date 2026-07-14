"use client";

import { useState } from "react";
import { api, type Approval } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { Card } from "@/components/Card";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { EmptyState } from "@/components/EmptyState";
import { PollingIndicator } from "@/components/PollingIndicator";
import { StatusBadge } from "@/components/StatusBadge";
import { formatDateTime } from "@/lib/format";

function ApprovalItem({
  approval,
  onDecided,
}: {
  approval: Approval;
  onDecided: () => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approved" | "denied" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const decide = async (decision: "approved" | "denied") => {
    setBusy(decision);
    setError(null);
    try {
      await api.decideApproval(approval.id, decision, note.trim() || undefined);
      onDecided();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <li className="rounded-md border border-zinc-800 bg-zinc-950/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-zinc-200">
            {approval.kind}
          </span>
          <StatusBadge status={approval.risk} />
        </div>
        <span className="text-xs text-zinc-600">
          requested {formatDateTime(approval.requested_at)}
        </span>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-zinc-400">
        {approval.description}
      </p>
      {error ? (
        <p role="alert" className="mt-2 text-xs text-red-400">
          {error}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label htmlFor={`note-${approval.id}`} className="sr-only">
          Decision note for {approval.kind}
        </label>
        <input
          id={`note-${approval.id}`}
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Optional note"
          maxLength={500}
          className="min-w-48 flex-1 rounded-md border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-600 focus:border-sky-500 focus:outline-none"
        />
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void decide("approved")}
          className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-4 py-1.5 text-sm font-medium text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
        >
          {busy === "approved" ? "Approving…" : "Approve"}
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void decide("denied")}
          className="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-1.5 text-sm font-medium text-red-300 hover:bg-red-500/20 disabled:opacity-50"
        >
          {busy === "denied" ? "Denying…" : "Deny"}
        </button>
      </div>
    </li>
  );
}

export default function ApprovalsPage() {
  const approvals = usePolling(() => api.listApprovals("pending"), 5000);

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Approvals</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Pending actions that require an operator decision before workers
            proceed.
          </p>
        </div>
        <PollingIndicator
          lastUpdated={approvals.lastUpdated}
          error={approvals.error}
        />
      </div>

      <ConnectionBanner visible={approvals.unreachable} />

      <Card>
        {approvals.data ? (
          approvals.data.items.length > 0 ? (
            <ul className="space-y-3">
              {approvals.data.items.map((approval) => (
                <ApprovalItem
                  key={approval.id}
                  approval={approval}
                  onDecided={approvals.refresh}
                />
              ))}
            </ul>
          ) : (
            <EmptyState message="No pending approvals." />
          )
        ) : (
          <p className="text-sm text-zinc-500">
            {approvals.unreachable ? "Unavailable." : "Loading…"}
          </p>
        )}
      </Card>
    </div>
  );
}
