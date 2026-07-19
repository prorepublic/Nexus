"use client";

import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

const DEFAULT_COST_MODE_TEXT =
  "Cost mode: subscription CLIs only — paid APIs disabled";

export function TopBar() {
  const health = usePolling(api.health, 5000);
  const system = usePolling(api.systemStatus, 5000);

  const healthy =
    !health.unreachable &&
    health.data?.status === "ok" &&
    health.data.database === "ok";
  const degraded =
    !health.unreachable &&
    health.data?.status === "ok" &&
    health.data.database !== "ok";

  const dotClass = healthy
    ? "bg-emerald-500"
    : degraded
      ? "bg-amber-500"
      : "bg-red-500";

  const healthLabel = health.loading
    ? "Connecting…"
    : healthy
      ? `Healthy · v${health.data?.version ?? "?"}`
      : degraded
        ? "Database unavailable"
        : "Control plane unreachable";

  const costMode = system.data?.cost_mode;
  const costText =
    costMode && costMode.paid_apis_enabled !== false
      ? "Cost mode: paid APIs enabled"
      : DEFAULT_COST_MODE_TEXT;

  return (
    <header className="sticky top-0 z-10 flex h-14 items-center justify-between gap-4 border-b border-zinc-800 bg-zinc-950/95 px-6 backdrop-blur">
      <div className="flex items-center gap-2">
        <span aria-hidden className={`h-2 w-2 rounded-full ${dotClass}`} />
        <span className="text-xs text-zinc-400">{healthLabel}</span>
      </div>
      <span
        title={costMode?.description ?? undefined}
        className="rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-xs font-medium whitespace-nowrap text-amber-300"
      >
        {costText}
      </span>
    </header>
  );
}
