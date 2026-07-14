import { API_BASE } from "@/lib/api";

/**
 * Shown when the control plane cannot be reached. Pages keep rendering their
 * last known data (if any) underneath.
 */
export function ConnectionBanner({ visible }: { visible: boolean }) {
  if (!visible) return null;
  return (
    <div
      role="alert"
      className="flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300"
    >
      <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-red-500" />
      Control plane unreachable at {API_BASE} — retrying automatically.
    </div>
  );
}
