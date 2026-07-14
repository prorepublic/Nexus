import type { ReactNode } from "react";

export function EmptyState({
  message,
  action,
}: {
  message: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-md border border-dashed border-zinc-800 px-4 py-8 text-center">
      <p className="text-sm text-zinc-500">{message}</p>
      {action}
    </div>
  );
}
