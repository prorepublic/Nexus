"use client";

export function PollingIndicator({
  lastUpdated,
  error,
}: {
  lastUpdated: Date | null;
  error?: string | null;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-zinc-500">
      <span
        aria-hidden
        className={`h-1.5 w-1.5 rounded-full ${
          error ? "bg-red-500" : "animate-pulse bg-emerald-500"
        }`}
      />
      {error
        ? "refresh failed"
        : lastUpdated
          ? `updated ${lastUpdated.toLocaleTimeString(undefined, {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            })}`
          : "loading"}
    </span>
  );
}
