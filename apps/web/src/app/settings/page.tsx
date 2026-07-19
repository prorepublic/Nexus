"use client";

import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { Card } from "@/components/Card";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { PollingIndicator } from "@/components/PollingIndicator";

function SettingRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <dt className="text-sm text-zinc-500">{label}</dt>
      <dd className="font-mono text-sm text-zinc-300">{value}</dd>
    </div>
  );
}

export default function SettingsPage() {
  const settings = usePolling(api.getSettings, 15000);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Settings</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Effective control plane configuration. Read-only.
          </p>
        </div>
        <PollingIndicator
          lastUpdated={settings.lastUpdated}
          error={settings.error}
        />
      </div>

      <ConnectionBanner visible={settings.unreachable} />

      <Card title="Execution policy">
        {settings.data ? (
          <>
            <dl className="divide-y divide-zinc-800/70">
              <SettingRow
                label="Review policy"
                value={settings.data.review_policy}
              />
              <SettingRow
                label="Planner mode"
                value={settings.data.planner_mode}
              />
              <SettingRow
                label="Max repair attempts"
                value={String(settings.data.max_repair_attempts)}
              />
              <SettingRow
                label="Task timeout"
                value={`${settings.data.task_timeout_seconds}s`}
              />
              <SettingRow
                label="Lease duration"
                value={`${settings.data.lease_seconds}s`}
              />
            </dl>
            <p className="mt-4 text-xs text-zinc-500">
              Values are configured via environment variables (NEXUS_*); see
              .env.example.
            </p>
          </>
        ) : (
          <p className="text-sm text-zinc-500">
            {settings.unreachable ? "Unavailable." : "Loading…"}
          </p>
        )}
      </Card>

      <Card title="Cost mode">
        <p className="text-sm text-amber-300">
          Cost mode: subscription CLIs only — paid APIs disabled
        </p>
        <p className="mt-2 text-xs text-zinc-500">
          {settings.data?.cost_mode.description ??
            "Workers run through locally installed subscription CLIs."}
        </p>
      </Card>
    </div>
  );
}
