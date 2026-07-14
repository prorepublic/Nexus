import type { ReactNode } from "react";

export function Card({
  title,
  action,
  children,
  className = "",
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-zinc-800 bg-zinc-900/60 ${className}`}
    >
      {title !== undefined || action !== undefined ? (
        <header className="flex items-center justify-between gap-3 border-b border-zinc-800/80 px-4 py-3">
          {title !== undefined ? (
            <h2 className="text-sm font-semibold text-zinc-200">{title}</h2>
          ) : (
            <span />
          )}
          {action}
        </header>
      ) : null}
      <div className="p-4">{children}</div>
    </section>
  );
}
