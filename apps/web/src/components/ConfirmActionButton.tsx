"use client";

import { useState } from "react";

/**
 * A destructive-action button with an inline confirm step:
 * first click arms it ("Confirm cancel?"), second click executes.
 */
export function ConfirmActionButton({
  label,
  confirmLabel,
  onConfirm,
  disabled = false,
}: {
  label: string;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
  disabled?: boolean;
}) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClick = async () => {
    if (!armed) {
      setArmed(true);
      setError(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      setArmed(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="inline-flex items-center gap-2">
      {error ? <span className="text-xs text-red-400">{error}</span> : null}
      {armed ? (
        <button
          type="button"
          onClick={() => setArmed(false)}
          disabled={busy}
          className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
        >
          Keep
        </button>
      ) : null}
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={disabled || busy}
        className={`rounded-md border px-3 py-1.5 text-xs font-medium disabled:opacity-50 ${
          armed
            ? "border-red-500/40 bg-red-500/15 text-red-300 hover:bg-red-500/25"
            : "border-zinc-700 text-zinc-300 hover:bg-zinc-800"
        }`}
      >
        {busy ? "Working…" : armed ? confirmLabel : label}
      </button>
    </span>
  );
}
