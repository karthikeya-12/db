"use client";

import { useEffect, useState } from "react";
import { LogOut } from "lucide-react";
import { api } from "@/lib/api";

export function useSignOut() {
  return async () => {
    await api.post("/api/auth/logout").catch(() => undefined);
    window.location.assign("/login");
  };
}

/** Signed-in email with a sign-out button, for the dark sidebar. */
export function UserMenu() {
  const [email, setEmail] = useState<string | null>(null);
  const signOut = useSignOut();

  useEffect(() => {
    api
      .get<{ user: { email: string } }>("/api/auth/me")
      .then((r) => setEmail(r.user.email))
      .catch(() => undefined);
  }, []);

  return (
    <div className="flex items-center justify-between gap-2 px-3 pb-3">
      <span className="truncate text-xs text-sidebar-muted" title={email ?? undefined}>
        {email ?? ""}
      </span>
      <button
        type="button"
        onClick={signOut}
        aria-label="Sign out"
        title="Sign out"
        className="rounded-md p-1.5 text-sidebar-muted transition-colors hover:bg-sidebar-active hover:text-sidebar-foreground"
      >
        <LogOut className="size-4" />
      </button>
    </div>
  );
}
