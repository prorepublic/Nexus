"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/goals", label: "Goals" },
  { href: "/runs", label: "Runs" },
  { href: "/approvals", label: "Approvals" },
  { href: "/repositories", label: "Repositories" },
  { href: "/pull-requests", label: "Pull Requests" },
  { href: "/settings", label: "Settings" },
] as const;

export function Sidebar() {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <aside className="flex w-52 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950">
      <div className="flex h-14 items-center border-b border-zinc-800 px-4">
        <Link href="/" className="flex items-baseline gap-2">
          <span className="text-sm font-semibold tracking-wide text-zinc-100">
            Nexus
          </span>
          <span className="text-xs text-zinc-500">control plane</span>
        </Link>
      </div>
      <nav aria-label="Primary" className="flex flex-col gap-1 p-2">
        {NAV_ITEMS.map((item) => {
          const active = isActive(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={`rounded-md px-3 py-2 text-sm font-medium ${
                active
                  ? "bg-zinc-800 text-zinc-100"
                  : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto border-t border-zinc-800 p-4">
        <p className="text-xs leading-relaxed text-zinc-600">
          Local-first AI development orchestration.
        </p>
      </div>
    </aside>
  );
}
