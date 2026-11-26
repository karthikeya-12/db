"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Loader2, RefreshCw, Unplug } from "lucide-react";
import { toast } from "sonner";
import { DocumentsTable } from "@/components/documents-table";
import { Monogram } from "@/components/monogram";
import { PageHeader } from "@/components/page-header";
import { StatTile } from "@/components/stat-tile";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { usePoll } from "@/hooks/use-poll";
import { api, ApiError } from "@/lib/api";
import { formatDuration, timeAgo } from "@/lib/format";
import type { ConnectionDetail, Item } from "@/lib/types";

const th = "px-5 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground";

export function ConnectionDetailView() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [syncing, setSyncing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  const { data, loading, error, reload } = usePoll(() => api.get<ConnectionDetail>(`/api/connectors/connections/${id}`), {
    intervalMs: 15_000,
    fastMs: 3_000,
    fast: syncing,
  });

  async function syncNow() {
    setSyncing(true);
    try {
      const result = await api.post<{ queued: boolean }>(`/api/connectors/connections/${id}/sync`);
      toast.success(result.queued ? "Looking for new data" : "A check is already waiting");
      await reload();
      setTimeout(() => setSyncing(false), 15_000); // poll quickly for a little while
    } catch (e) {
      setSyncing(false);
      toast.error(e instanceof ApiError ? e.message : "Could not start a sync");
    }
  }

  async function setEnabled(enabled: boolean) {
    try {
      await api.post(`/api/connectors/connections/${id}`, { enabled });
      toast.success(enabled ? "Syncing resumed" : "Syncing paused");
      void reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not update");
    }
  }

  async function disconnect() {
    setDisconnecting(true);
    try {
      await fetch(`/api/connectors/connections/${id}`, { method: "DELETE" }).then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Could not disconnect");
      });
      toast.success("Disconnected. Documents already filed stay in the library.");
      router.push("/connectors");
    } catch (e) {
      setDisconnecting(false);
      toast.error(e instanceof Error ? e.message : "Could not disconnect");
    }
  }

  if (loading && !data) return <Skeleton className="h-64 w-full" />;
  if (error && !data) {
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-5 py-4 text-sm text-destructive">
        This connection could not be found. <Link href="/connectors" className="underline">Back to connectors</Link>
      </div>
    );
  }
  if (!data) return null;

  const { connection, stats } = data;
  const status =
    connection.status === "ERROR" ? (
      <Badge variant="destructive">Needs reconnect</Badge>
    ) : !connection.enabled ? (
      <Badge variant="warning">Paused</Badge>
    ) : (
      <Badge variant="success">Connected</Badge>
    );
  const items: Item[] = data.recent_items.map((i) => ({
    item_id: i.item_id,
    source_id: connection.source_id,
    content_type: "",
    file_name: i.file_name,
    status: i.status,
    error_reason: null,
    created_at: null,
    updated_at: i.updated_at,
    completed_at: null,
  }));

  return (
    <>
      <Link href="/connectors" className="mb-6 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> All connectors
      </Link>
      <PageHeader
        eyebrow="Connector"
        title={data.name}
        description={
          connection.last_synced_at ? `Last checked ${timeAgo(connection.last_synced_at)}.` : "Waiting for the first check."
        }
        actions={
          <div className="flex items-center gap-3">
            <Monogram name={data.name} />
            {status}
          </div>
        }
      />

      {connection.last_error && (
        <p className="mb-6 rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">{connection.last_error}</p>
      )}

      <div className="mb-8 flex flex-wrap items-center gap-4 rounded-lg border bg-card px-5 py-4">
        <Button onClick={syncNow} disabled={syncing || connection.status !== "ACTIVE" || !connection.enabled}>
          {syncing ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          Sync now
        </Button>
        <label className="flex items-center gap-2.5 text-sm">
          <Switch checked={connection.enabled} onCheckedChange={setEnabled} aria-label="Sync automatically" />
          Sync automatically
        </label>
        <div className="ml-auto">
          {confirming ? (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Disconnect {data.name}? Filed documents stay.</span>
              <Button size="sm" variant="destructive" onClick={disconnect} disabled={disconnecting}>
                {disconnecting && <Loader2 className="animate-spin" />}
                Disconnect
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={disconnecting}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button variant="outline" onClick={() => setConfirming(true)}>
              <Unplug /> Disconnect
            </Button>
          )}
        </div>
      </div>

      <div className="mb-10 grid grid-cols-3 gap-4">
        <StatTile label="Filed" value={stats.documents} hint="documents in the library" />
        <StatTile label="In progress" value={stats.in_progress} hint="queued or processing" />
        <StatTile label="Failed" value={stats.failed} hint="see the Ledger to retry" tone={stats.failed ? "alert" : undefined} />
      </div>

      <section className="mb-10">
        <h2 className="mb-3 font-serif text-2xl font-semibold">Recent documents</h2>
        <DocumentsTable items={items} loading={false} />
      </section>

      {data.recent_runs.length > 0 && (
        <section>
          <h2 className="mb-3 font-serif text-2xl font-semibold">Recent checks</h2>
          <div className="overflow-hidden rounded-lg border bg-card">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/60">
                  <th className={th}>Started</th>
                  <th className={`${th} text-right`}>Found</th>
                  <th className={`${th} text-right`}>Filed</th>
                  <th className={`${th} text-right`}>Failed</th>
                  <th className={`${th} hidden text-right sm:table-cell`}>Took</th>
                </tr>
              </thead>
              <tbody className="divide-y tabular-nums">
                {data.recent_runs.map((run) => (
                  <tr key={run.run_id}>
                    <td className="px-5 py-3">{timeAgo(run.started_at)}</td>
                    <td className="px-5 py-3 text-right">{run.items_identified}</td>
                    <td className="px-5 py-3 text-right">{run.items_processed}</td>
                    <td className="px-5 py-3 text-right">{run.items_failed || <span className="text-muted-foreground">0</span>}</td>
                    <td className="hidden px-5 py-3 text-right text-muted-foreground sm:table-cell">{formatDuration(run.duration_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
