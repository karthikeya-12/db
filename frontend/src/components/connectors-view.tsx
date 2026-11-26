"use client";

import { useMemo, useRef, useState } from "react";
import { AlertTriangle, Search } from "lucide-react";
import { toast } from "sonner";
import { ProviderCard } from "@/components/provider-card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { usePoll } from "@/hooks/use-poll";
import { api } from "@/lib/api";
import type { CatalogProvider, CatalogResponse, ConnectionInfo } from "@/lib/types";

export function ConnectorsView() {
  const [query, setQuery] = useState("");
  const [connectedOnly, setConnectedOnly] = useState(false);
  const [category, setCategory] = useState("all");
  const [watching, setWatching] = useState(false); // true while the Connect window is open: refresh quickly
  const [connectingKey, setConnectingKey] = useState<string | null>(null);
  const popupTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const { data, loading, error, reload } = usePoll(() => api.get<CatalogResponse>("/api/connectors/catalog"), {
    intervalMs: 30_000,
    fastMs: 3_000,
    fast: watching,
  });

  async function connect(provider: CatalogProvider) {
    if (!provider.integration_key) return;
    // Open the window now (inside the click) so pop-up blockers allow it; point it at Nango once we have the link.
    const popup = window.open("", "nango-connect", "width=520,height=760");
    if (!popup) {
      toast.error("Your browser blocked the sign-in window. Allow pop-ups for this site and try again.");
      return;
    }
    setConnectingKey(provider.provider);
    try {
      const session = await api.post<{ connect_link: string | null; token: string }>("/api/connectors/session", {
        integration_key: provider.integration_key,
      });
      if (!session.connect_link) throw new Error("Nango did not return a sign-in link");
      popup.location.href = session.connect_link;
    } catch (e) {
      popup.close();
      setConnectingKey(null);
      toast.error(e instanceof Error ? e.message : "Could not start the sign-in");
      return;
    }
    setWatching(true);
    if (popupTimer.current) clearInterval(popupTimer.current);
    popupTimer.current = setInterval(() => {
      if (popup.closed) {
        if (popupTimer.current) clearInterval(popupTimer.current);
        setWatching(false);
        setConnectingKey(null);
        void reload();
      }
    }, 700);
  }

  async function toggle(connection: ConnectionInfo, enabled: boolean) {
    try {
      await api.post(`/api/connectors/connections/${connection.id}`, { enabled });
      toast.success(enabled ? "Syncing resumed" : "Syncing paused");
      void reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not update");
    }
  }

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const visible = (data?.providers ?? []).filter((p) => {
      if (connectedOnly && p.connections.length === 0) return false;
      if (category !== "all" && p.category !== category) return false;
      return !q || `${p.name} ${p.description} ${p.category}`.toLowerCase().includes(q);
    });
    return (data?.categories ?? [])
      .map((name) => ({ name, items: visible.filter((p) => p.category === name) }))
      .filter((group) => group.items.length > 0);
  }, [data, query, connectedOnly, category]);

  return (
    <div className="space-y-8">
      {data && !data.nango.ok && (
        <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-5 py-4 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <div>
            <p className="font-medium">Nango is not reachable, so connecting is unavailable.</p>
            <p className="mt-0.5 opacity-80">{data.nango.error}</p>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search connectors" className="pl-9" />
        </div>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          aria-label="Filter by category"
          className="h-10 rounded-md border border-input bg-card px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
        >
          <option value="all">All categories</option>
          {(data?.categories ?? []).map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <label className="ml-auto flex cursor-pointer items-center gap-2.5 text-sm">
          <Switch checked={connectedOnly} onCheckedChange={setConnectedOnly} aria-label="Connected only" />
          Connected only
        </label>
      </div>

      {loading && !data ? (
        <div className="grid gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-44" />
          ))}
        </div>
      ) : error && !data ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-5 py-4 text-sm text-destructive">
          Could not reach the service. Make sure the API is running, then refresh.
        </div>
      ) : groups.length === 0 ? (
        <div className="rounded-lg border border-dashed border-input px-6 py-14 text-center font-serif text-xl italic text-muted-foreground">
          {connectedOnly ? "Nothing is connected yet." : "No connectors match your search."}
        </div>
      ) : (
        groups.map((group) => (
          <section key={group.name}>
            <h2 className="mb-3 flex items-baseline gap-2 font-serif text-2xl font-semibold">
              {group.name}
              <span className="text-base font-normal text-muted-foreground">({group.items.length})</span>
            </h2>
            <div className="grid gap-4 md:grid-cols-2">
              {group.items.map((provider) => (
                <ProviderCard
                  key={provider.provider}
                  provider={provider}
                  connecting={connectingKey === provider.provider}
                  onConnect={connect}
                  onToggle={toggle}
                />
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}
