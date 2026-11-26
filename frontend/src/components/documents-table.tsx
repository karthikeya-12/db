"use client";

import { AlertTriangle, CheckCircle2, Clock, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { displayName, extensionOf, sourceLabel, timeAgo } from "@/lib/format";
import type { Item, ItemStatus } from "@/lib/types";

const STATUS: Record<ItemStatus, { label: string; variant: "success" | "warning" | "destructive" | "neutral" | "accent"; icon: React.ReactNode }> = {
  DONE: { label: "Filed", variant: "success", icon: <CheckCircle2 className="size-3" /> },
  PROCESSING: { label: "Processing", variant: "accent", icon: <Loader2 className="size-3 animate-spin" /> },
  NEW: { label: "Queued", variant: "neutral", icon: <Clock className="size-3" /> },
  FAILED: { label: "Failed", variant: "destructive", icon: <AlertTriangle className="size-3" /> },
  DUPLICATE: { label: "Duplicate", variant: "neutral", icon: <CheckCircle2 className="size-3" /> },
};

export function DocumentsTable({ items, loading }: { items: Item[]; loading: boolean }) {
  if (loading) {
    return (
      <div className="space-y-px overflow-hidden rounded-lg border bg-card">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex items-center gap-4 px-5 py-4">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="ml-auto h-4 w-24" />
          </div>
        ))}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="rounded-lg border bg-card px-6 py-14 text-center">
        <p className="font-serif text-2xl italic text-muted-foreground">The shelves are empty.</p>
        <p className="mt-2 text-sm text-muted-foreground">Add a document above, or connect a source, and it will appear here.</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border bg-card shadow-[0_1px_2px_oklch(0.3_0.05_90/0.06)]">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/60 text-left text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            <th className="px-5 py-3">Document</th>
            <th className="hidden px-5 py-3 sm:table-cell">Source</th>
            <th className="px-5 py-3">Status</th>
            <th className="hidden px-5 py-3 text-right md:table-cell">Updated</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {items.map((item) => {
            const name = displayName(item);
            const status = STATUS[item.status] ?? STATUS.NEW;
            return (
              <tr key={item.item_id} className="transition-colors hover:bg-accent/40">
                <td className="w-1/2 max-w-0 px-5 py-3.5">
                  <div className="flex items-center gap-3">
                    <span className="flex h-8 w-10 shrink-0 items-center justify-center rounded-sm border border-brass/40 text-[10px] font-semibold tracking-wider text-brass">
                      {extensionOf(name)}
                    </span>
                    <div className="min-w-0">
                      <p className="truncate font-medium">{name}</p>
                      {item.status === "FAILED" && item.error_reason && (
                        <p className="truncate text-xs text-destructive">{item.error_reason}</p>
                      )}
                    </div>
                  </div>
                </td>
                <td className="hidden px-5 py-3.5 text-muted-foreground sm:table-cell">{sourceLabel(item.source_id)}</td>
                <td className="px-5 py-3.5">
                  <Badge variant={status.variant}>
                    {status.icon}
                    {status.label}
                  </Badge>
                </td>
                <td className="hidden px-5 py-3.5 text-right text-muted-foreground md:table-cell">{timeAgo(item.updated_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
