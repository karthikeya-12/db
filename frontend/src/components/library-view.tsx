"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { DocumentsTable } from "@/components/documents-table";
import { UploadDropzone } from "@/components/upload-dropzone";
import { Input } from "@/components/ui/input";
import { usePoll } from "@/hooks/use-poll";
import { api } from "@/lib/api";
import { displayName, sourceLabel } from "@/lib/format";
import type { Item, UploadFormats } from "@/lib/types";

const ACTIVE = new Set(["NEW", "PROCESSING"]);

export function LibraryView() {
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("all");

  const { data: formats } = usePoll(() => api.get<UploadFormats>("/api/uploads/formats"), { intervalMs: 600_000 });
  // Poll quickly while anything is still being processed, slowly otherwise.
  const [fast, setFast] = useState(false);
  const { data: items, loading, error, reload } = usePoll(
    async () => {
      const rows = await api.get<Item[]>("/api/items?limit=500");
      setFast(rows.some((r) => ACTIVE.has(r.status)));
      return rows;
    },
    { fast },
  );

  const sources = useMemo(() => Array.from(new Set((items ?? []).map((i) => i.source_id))), [items]);
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (items ?? []).filter(
      (i) => (source === "all" || i.source_id === source) && (!q || displayName(i).toLowerCase().includes(q)),
    );
  }, [items, query, source]);

  return (
    <div className="space-y-8">
      <UploadDropzone
        formats={formats}
        onUploaded={() => {
          setFast(true);
          void reload();
        }}
      />

      <section>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <h2 className="mr-auto font-serif text-2xl font-semibold">
            Collection{items ? <span className="ml-2 text-base font-normal text-muted-foreground">({visible.length})</span> : null}
          </h2>
          <div className="relative w-full sm:w-64">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search documents" className="pl-9" />
          </div>
          {sources.length > 1 && (
            <select
              value={source}
              onChange={(e) => setSource(e.target.value)}
              className="h-10 rounded-md border border-input bg-card px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
              aria-label="Filter by source"
            >
              <option value="all">All sources</option>
              {sources.map((s) => (
                <option key={s} value={s}>
                  {sourceLabel(s)}
                </option>
              ))}
            </select>
          )}
        </div>
        {error && !items ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-5 py-4 text-sm text-destructive">
            Could not reach the service. Make sure the API is running, then refresh.
          </div>
        ) : (
          <DocumentsTable items={visible} loading={loading} />
        )}
      </section>
    </div>
  );
}
