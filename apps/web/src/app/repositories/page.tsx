"use client";

import { useState } from "react";
import { api, type Repository, type TrustLevel } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { Card } from "@/components/Card";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { EmptyState } from "@/components/EmptyState";
import { PollingIndicator } from "@/components/PollingIndicator";
import { StatusBadge } from "@/components/StatusBadge";

const TRUST_LEVELS: Array<{ value: TrustLevel; label: string }> = [
  { value: "untrusted", label: "Untrusted" },
  { value: "reviewed", label: "Reviewed" },
  { value: "trusted-local", label: "Trusted (local)" },
  { value: "trusted-owner-approved", label: "Trusted (owner approved)" },
];

const inputClass =
  "rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:border-sky-500 focus:outline-none";

function RegisterRepositoryForm({ onRegistered }: { onRegistered: () => void }) {
  const [source, setSource] = useState("");
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!source.trim()) {
      setError("Source is required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await api.registerRepository({
        source: source.trim(),
        ...(name.trim() ? { name: name.trim() } : {}),
      });
      setSource("");
      setName("");
      onRegistered();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={(e) => void handleSubmit(e)} noValidate>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-64 flex-1">
          <label
            htmlFor="repo-source"
            className="mb-1.5 block text-sm font-medium text-zinc-300"
          >
            Source <span className="text-red-400">*</span>
          </label>
          <input
            id="repo-source"
            type="text"
            required
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder="/path/to/repo or org/repo"
            className={`${inputClass} w-full`}
            aria-invalid={Boolean(error)}
          />
        </div>
        <div className="min-w-48">
          <label
            htmlFor="repo-name"
            className="mb-1.5 block text-sm font-medium text-zinc-300"
          >
            Name <span className="text-zinc-600">(optional)</span>
          </label>
          <input
            id="repo-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Defaults to the repo name"
            className={`${inputClass} w-full`}
          />
        </div>
        <button
          type="submit"
          disabled={submitting}
          className="rounded-md bg-zinc-100 px-4 py-2 text-sm font-medium text-zinc-900 hover:bg-white disabled:opacity-50"
        >
          {submitting ? "Registering…" : "Register"}
        </button>
      </div>
      {error ? (
        <p role="alert" className="mt-2 text-xs text-red-400">
          {error}
        </p>
      ) : null}
    </form>
  );
}

function TrustLevelSelector({
  repository,
  onChanged,
}: {
  repository: Repository;
  onChanged: () => void;
}) {
  const [level, setLevel] = useState<TrustLevel>(repository.trust_level);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.setRepositoryTrust(repository.id, level);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="flex items-center gap-2">
        <label htmlFor={`trust-${repository.id}`} className="sr-only">
          Trust level for {repository.name}
        </label>
        <select
          id={`trust-${repository.id}`}
          value={level}
          onChange={(e) => setLevel(e.target.value as TrustLevel)}
          className={inputClass}
        >
          {TRUST_LEVELS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={busy || level === repository.trust_level}
          onClick={() => void apply()}
          className="rounded-md border border-zinc-700 px-3 py-2 text-xs font-medium text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
        >
          {busy ? "Applying…" : "Apply"}
        </button>
      </div>
      {error ? (
        <p role="alert" className="mt-1 text-xs text-red-400">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export default function RepositoriesPage() {
  const repos = usePolling(api.listRepositories, 5000);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Repositories</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Repositories registered with the control plane. Trust levels decide
            how much autonomy workers get inside each repo.
          </p>
        </div>
        <PollingIndicator lastUpdated={repos.lastUpdated} error={repos.error} />
      </div>

      <ConnectionBanner visible={repos.unreachable} />

      <Card title="Register repository">
        <RegisterRepositoryForm onRegistered={repos.refresh} />
      </Card>

      <Card title={`Registered (${repos.data?.items.length ?? "…"})`}>
        {repos.data ? (
          repos.data.items.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-zinc-800 text-xs text-zinc-500">
                    <th className="py-2 pr-4 font-medium">Name</th>
                    <th className="py-2 pr-4 font-medium">Trust</th>
                    <th className="py-2 pr-4 font-medium">Onboarded</th>
                    <th className="py-2 pr-4 font-medium">Default branch</th>
                    <th className="py-2 pr-4 font-medium">Languages</th>
                    <th className="py-2 pr-4 font-medium">Validation</th>
                    <th className="py-2 font-medium">Change trust</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/70">
                  {repos.data.items.map((repo) => (
                    <tr key={repo.id}>
                      <td className="max-w-xs py-2.5 pr-4">
                        <span className="block truncate font-medium text-zinc-200">
                          {repo.name}
                        </span>
                        <span className="block truncate font-mono text-xs text-zinc-600">
                          {repo.github_slug ?? repo.local_path ?? ""}
                        </span>
                      </td>
                      <td className="py-2.5 pr-4">
                        <StatusBadge status={repo.trust_level} />
                      </td>
                      <td className="py-2.5 pr-4">
                        <span
                          className={
                            repo.onboarded
                              ? "text-emerald-400"
                              : "text-zinc-600"
                          }
                        >
                          {repo.onboarded ? "yes" : "no"}
                        </span>
                      </td>
                      <td className="py-2.5 pr-4 font-mono text-xs text-zinc-400">
                        {repo.default_branch}
                      </td>
                      <td className="py-2.5 pr-4 text-zinc-400">
                        {repo.languages.length > 0
                          ? repo.languages.join(", ")
                          : "—"}
                      </td>
                      <td className="py-2.5 pr-4 text-zinc-400">
                        {repo.validation_kinds.length > 0
                          ? repo.validation_kinds.join(", ")
                          : "—"}
                      </td>
                      <td className="py-2.5">
                        <TrustLevelSelector
                          repository={repo}
                          onChanged={repos.refresh}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState message="No repositories registered yet. Register one above to let goals target it." />
          )
        ) : (
          <p className="text-sm text-zinc-500">
            {repos.unreachable ? "Unavailable." : "Loading…"}
          </p>
        )}
      </Card>
    </div>
  );
}
