"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { StatTile } from "@/components/stat-tile";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePoll } from "@/hooks/use-poll";
import { api } from "@/lib/api";
import { formatDuration, itemNameFromId, sourceLabel, stageLabel, timeAgo } from "@/lib/format";
import type { DeadLetter, Job, Run } from "@/lib/types";

const SCAN_SOURCES = ["upload", "local_folder"];
const panel = "overflow-hidden rounded-lg border bg-card shadow-[0_1px_2px_oklch(0.3_0.05_90/0.06)]";
const th = "px-5 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground";

function SectionTitle({ children, aside }: { children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-baseline justify-between">
      <h2 className="font-serif text-2xl font-semibold">{children}</h2>
      {aside}
    </div>
  );
}

export function LedgerView() {
  const [fast, setFast] = useState(false);
  const runs = usePoll(() => api.get<Run[]>("/api/runs?limit=25"), { intervalMs: 10_000, fast });
  const letters = usePoll(() => api.get<DeadLetter[]>("/api/dead-letters?limit=100"), { intervalMs: 10_000, fast });
  const jobs = usePoll(
    async () => {
      const rows = await api.get<Job[]>("/api/jobs?limit=10");
      setFast(rows.some((j) => j.status === "QUEUED" || j.status === "RUNNING"));
      return rows;
    },
    { intervalMs: 10_000, fast },
  );
  const [retrying, setRetrying] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);

  const refreshAll = () => {
    void runs.reload();
    void letters.reload();
    void jobs.reload();
  };

  const open = useMemo(() => (letters.data ?? []).filter((l) => !l.requeued_at), [letters.data]);
  const totals = useMemo(() => {
    const day = Date.now() - 86_400_000;
    const recent = (runs.data ?? []).filter((r) => r.started_at && new Date(r.started_at).getTime() >= day);
    return { processed: recent.reduce((n, r) => n + r.items_processed, 0), runs: recent.length };
  }, [runs.data]);
  const busy = (jobs.data ?? []).filter((j) => j.status === "QUEUED" || j.status === "RUNNING").length;

  async function retry(letter: DeadLetter) {
    setRetrying(letter.id);
    try {
      await api.post(`/api/dead-letters/${letter.id}/retry`);
      toast.success(`${itemNameFromId(letter.item_id)} sent back for another attempt`);
      setFast(true);
      refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not retry");
    } finally {
      setRetrying(null);
    }
  }

  async function scanNow() {
    setScanning(true);
    try {
      await Promise.all(SCAN_SOURCES.map((s) => api.post(`/api/scan?source_id=${encodeURIComponent(s)}`)));
      toast.success("Looking for new documents");
      setFast(true);
      refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not start a scan");
    } finally {
      setScanning(false);
    }
  }

  const loading = runs.loading || letters.loading;

  return (
    <div className="space-y-10">
      <div className="flex items-center justify-end">
        <Button variant="outline" onClick={scanNow} disabled={scanning}>
          {scanning ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          Check for new documents
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatTile label="Filed today" value={loading ? "–" : totals.processed} hint="documents, last 24 hours" />
        <StatTile label="Runs today" value={loading ? "–" : totals.runs} hint="ingestion runs" />
        <StatTile label="In progress" value={loading ? "–" : busy} hint="queued or running" />
        <StatTile label="Needs attention" value={loading ? "–" : open.length} hint="failed documents" tone={open.length ? "alert" : undefined} />
      </div>

      <section>
        <SectionTitle>Needs attention</SectionTitle>
        {loading ? (
          <Skeleton className="h-24 w-full" />
        ) : open.length === 0 ? (
          <div className="flex items-center gap-3 rounded-lg border bg-card px-5 py-5 text-sm text-muted-foreground">
            <CheckCircle2 className="size-5 text-success" />
            Nothing is waiting on you. Every document has been filed.
          </div>
        ) : (
          <ul className={`${panel} divide-y`}>
            {open.map((letter) => (
              <li key={letter.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4">
                <AlertTriangle className="size-4 shrink-0 text-destructive" />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{itemNameFromId(letter.item_id)}</p>
                  <p className="truncate text-xs text-destructive" title={letter.error_message}>
                    {stageLabel(letter.stage_failed)}: {letter.error_message}
                  </p>
                </div>
                <span className="text-xs text-muted-foreground">
                  {letter.attempt_count} {letter.attempt_count === 1 ? "attempt" : "attempts"} · {timeAgo(letter.failed_at)}
                </span>
                <Button size="sm" variant="outline" disabled={retrying === letter.id} onClick={() => retry(letter)}>
                  {retrying === letter.id ? <Loader2 className="animate-spin" /> : <RotateCcw />}
                  Retry
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <SectionTitle>Recent runs</SectionTitle>
        {loading ? (
          <Skeleton className="h-40 w-full" />
        ) : (runs.data ?? []).length === 0 ? (
          <div className="rounded-lg border bg-card px-6 py-12 text-center font-serif text-xl italic text-muted-foreground">
            No runs have been recorded yet.
          </div>
        ) : (
          <div className={panel}>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/60">
                  <th className={th}>Started</th>
                  <th className={`${th} hidden sm:table-cell`}>Source</th>
                  <th className={`${th} text-right`}>Found</th>
                  <th className={`${th} text-right`}>Filed</th>
                  <th className={`${th} hidden text-right md:table-cell`}>Unchanged</th>
                  <th className={`${th} text-right`}>Failed</th>
                  <th className={`${th} hidden text-right md:table-cell`}>Took</th>
                </tr>
              </thead>
              <tbody className="divide-y tabular-nums">
                {(runs.data ?? []).map((run) => (
                  <tr key={run.run_id} className="hover:bg-accent/40">
                    <td className="px-5 py-3">{timeAgo(run.started_at)}</td>
                    <td className="hidden px-5 py-3 text-muted-foreground sm:table-cell">{sourceLabel(run.source_id)}</td>
                    <td className="px-5 py-3 text-right">{run.items_identified}</td>
                    <td className="px-5 py-3 text-right">{run.items_processed}</td>
                    <td className="hidden px-5 py-3 text-right text-muted-foreground md:table-cell">{run.items_skipped_duplicate}</td>
                    <td className="px-5 py-3 text-right">
                      {run.items_failed ? <Badge variant="destructive">{run.items_failed}</Badge> : <span className="text-muted-foreground">0</span>}
                    </td>
                    <td className="hidden px-5 py-3 text-right text-muted-foreground md:table-cell">{formatDuration(run.duration_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
