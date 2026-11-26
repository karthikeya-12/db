"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookOpen, History, LogOut, MessageSquareText, Plug } from "lucide-react";
import { cn } from "@/lib/utils";
import { ApiStatus } from "@/components/api-status";
import { UserMenu, useSignOut } from "@/components/user-menu";

export const NAV_ITEMS = [
  { href: "/data", label: "Library", icon: BookOpen },
  { href: "/connectors", label: "Connectors", icon: Plug },
  { href: "/chat", label: "Consult", icon: MessageSquareText },
  { href: "/activity", label: "Ledger", icon: History },
];

export function Brand({ tone = "dark" }: { tone?: "dark" | "light" }) {
  return (
    <div className="flex items-center gap-3">
      <div
        className={cn(
          "flex size-9 items-center justify-center rounded-full border font-serif text-xl font-semibold",
          tone === "dark" ? "border-brass text-brass" : "border-brass text-primary",
        )}
      >
        A
      </div>
      <div className="leading-tight">
        <div className={cn("font-serif text-[22px] font-semibold tracking-tight", tone === "dark" && "text-sidebar-foreground")}>
          The Archive
        </div>
        <div className="text-[10px] font-medium uppercase tracking-[0.24em] text-brass">Private library</div>
      </div>
    </div>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col bg-sidebar px-4 py-7 text-sidebar-foreground md:flex">
      <div className="px-2 pb-8">
        <Brand />
      </div>
      <div className="mx-2 mb-4 h-px bg-brass/30" />
      <nav className="flex flex-1 flex-col gap-1">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 rounded-md border-l-2 px-3 py-2.5 text-sm tracking-wide transition-colors",
                active
                  ? "border-brass bg-sidebar-active font-medium text-sidebar-foreground"
                  : "border-transparent text-sidebar-muted hover:bg-sidebar-active/60 hover:text-sidebar-foreground",
              )}
            >
              <Icon className={cn("size-4", active && "text-brass")} />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="mx-2 mb-3 h-px bg-brass/30" />
      <UserMenu />
      <div className="px-3">
        <ApiStatus />
      </div>
    </aside>
  );
}

/** Compact navigation for narrow screens. */
export function MobileNav() {
  const pathname = usePathname();
  const signOut = useSignOut();
  return (
    <header className="sticky top-0 z-10 flex items-center justify-between gap-4 bg-sidebar px-4 py-3 md:hidden">
      <Brand />
      <nav className="flex gap-1">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              aria-label={label}
              className={cn("rounded-md p-2", active ? "bg-sidebar-active text-brass" : "text-sidebar-muted")}
            >
              <Icon className="size-4" />
            </Link>
          );
        })}
        <button type="button" onClick={signOut} aria-label="Sign out" className="rounded-md p-2 text-sidebar-muted">
          <LogOut className="size-4" />
        </button>
      </nav>
    </header>
  );
}
