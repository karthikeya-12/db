"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Small "API online" indicator; polls the backend health endpoint. */
export function ApiStatus() {
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = () =>
      api
        .get<{ status: string }>("/api/health")
        .then(() => !cancelled && setOnline(true))
        .catch(() => !cancelled && setOnline(false));
    check();
    const timer = setInterval(check, 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="flex items-center gap-2 text-xs text-sidebar-muted">
      <span
        className={cn(
          "size-1.5 rounded-full",
          online === null && "bg-sidebar-muted/50",
          online === true && "bg-[oklch(0.75_0.12_150)]",
          online === false && "bg-[oklch(0.65_0.15_25)]",
        )}
      />
      {online === null ? "Checking service" : online ? "Service online" : "Service unreachable"}
    </div>
  );
}
