const STATUS_STYLES: Record<string, string> = {
  // neutral / not started
  draft: "bg-zinc-500/10 text-zinc-400 border-zinc-500/25",
  pending: "bg-zinc-500/10 text-zinc-400 border-zinc-500/25",
  queued: "bg-zinc-500/10 text-zinc-300 border-zinc-500/25",
  cancelled: "bg-zinc-500/10 text-zinc-500 border-zinc-500/20",

  // in progress
  planning: "bg-sky-500/10 text-sky-400 border-sky-500/25",
  ready: "bg-sky-500/10 text-sky-400 border-sky-500/25",
  executing: "bg-blue-500/10 text-blue-400 border-blue-500/25",
  running: "bg-blue-500/10 text-blue-400 border-blue-500/25",
  validating: "bg-indigo-500/10 text-indigo-400 border-indigo-500/25",
  repairing: "bg-amber-500/10 text-amber-400 border-amber-500/25",

  // needs a human
  review: "bg-violet-500/10 text-violet-400 border-violet-500/25",
  blocked: "bg-amber-500/10 text-amber-400 border-amber-500/25",

  // terminal
  completed: "bg-emerald-500/10 text-emerald-400 border-emerald-500/25",
  succeeded: "bg-emerald-500/10 text-emerald-400 border-emerald-500/25",
  approved: "bg-emerald-500/10 text-emerald-400 border-emerald-500/25",
  passed: "bg-emerald-500/10 text-emerald-400 border-emerald-500/25",
  failed: "bg-red-500/10 text-red-400 border-red-500/25",
  timed_out: "bg-red-500/10 text-red-400 border-red-500/25",
  denied: "bg-red-500/10 text-red-400 border-red-500/25",
  error: "bg-red-500/10 text-red-400 border-red-500/25",
  skipped: "bg-zinc-500/10 text-zinc-400 border-zinc-500/25",

  // risk levels / finding severities
  low: "bg-zinc-500/10 text-zinc-400 border-zinc-500/25",
  medium: "bg-amber-500/10 text-amber-400 border-amber-500/25",
  high: "bg-red-500/10 text-red-400 border-red-500/25",
  critical: "bg-red-500/20 text-red-300 border-red-500/40",
  info: "bg-sky-500/10 text-sky-400 border-sky-500/25",

  // repository trust levels
  untrusted: "bg-red-500/10 text-red-400 border-red-500/25",
  reviewed: "bg-amber-500/10 text-amber-400 border-amber-500/25",
  "trusted-local": "bg-sky-500/10 text-sky-400 border-sky-500/25",
  "trusted-owner-approved":
    "bg-emerald-500/10 text-emerald-400 border-emerald-500/25",

  // pull request states
  open: "bg-emerald-500/10 text-emerald-400 border-emerald-500/25",
  merged: "bg-violet-500/10 text-violet-400 border-violet-500/25",
  closed: "bg-zinc-500/10 text-zinc-500 border-zinc-500/20",

  // health / availability
  ok: "bg-emerald-500/10 text-emerald-400 border-emerald-500/25",
  available: "bg-emerald-500/10 text-emerald-400 border-emerald-500/25",
  down: "bg-red-500/10 text-red-400 border-red-500/25",
  unavailable: "bg-red-500/10 text-red-400 border-red-500/25",
};

const FALLBACK_STYLE = "bg-zinc-500/10 text-zinc-400 border-zinc-500/25";

const ACTIVE_STATUSES = new Set(["running", "executing", "validating"]);

export function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? FALLBACK_STYLE;
  const label = status.replaceAll("_", " ");
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${style}`}
    >
      {ACTIVE_STATUSES.has(status) ? (
        <span
          aria-hidden
          className="h-1.5 w-1.5 animate-pulse rounded-full bg-current"
        />
      ) : null}
      {label}
    </span>
  );
}
