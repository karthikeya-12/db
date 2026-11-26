"use client";

import { useCallback, useRef, useState } from "react";
import { Loader2, UploadCloud } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import type { UploadFormats, UploadResult } from "@/lib/types";
import { cn } from "@/lib/utils";

export function UploadDropzone({ formats, onUploaded }: { formats: UploadFormats | null; onUploaded: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);

  const send = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      setBusy(true);
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      let results: UploadResult[] = [];
      try {
        results = (await api.upload<{ files: UploadResult[] }>("/api/uploads", form)).files;
      } catch (e) {
        // Every file was refused: the API answers 4xx with the per-file reasons.
        const detail = e instanceof ApiError && Array.isArray(e.detail) ? (e.detail as UploadResult[]) : null;
        if (detail) results = detail;
        else toast.error(e instanceof Error ? e.message : "Upload failed");
      }
      const accepted = results.filter((r) => r.status === "queued");
      results.filter((r) => r.status === "rejected").forEach((r) => toast.error(`${r.file_name}: ${r.error}`));
      if (accepted.length) {
        toast.success(accepted.length === 1 ? `${accepted[0].file_name} added to the library` : `${accepted.length} documents added to the library`);
        onUploaded();
      }
      setBusy(false);
    },
    [onUploaded],
  );

  const accept = formats?.extensions.join(",");
  const summary = formats
    ? `${formats.extensions.slice(0, 8).map((e) => e.slice(1).toUpperCase()).join(", ")} and more · up to ${formats.max_mb} MB each`
    : "";

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        void send(Array.from(e.dataTransfer.files));
      }}
      onClick={() => !busy && inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && !busy && inputRef.current?.click()}
      className={cn(
        "flex cursor-pointer flex-col items-center gap-3 rounded-lg border border-dashed px-6 py-10 text-center transition-colors",
        dragging ? "border-brass bg-accent" : "border-input bg-card/60 hover:border-brass hover:bg-card",
        busy && "cursor-progress opacity-80",
      )}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={accept}
        className="hidden"
        onChange={(e) => {
          void send(Array.from(e.target.files ?? []));
          e.target.value = "";
        }}
      />
      <div className="flex size-11 items-center justify-center rounded-full border border-brass/50 text-brass">
        {busy ? <Loader2 className="size-5 animate-spin" /> : <UploadCloud className="size-5" />}
      </div>
      <div>
        <p className="font-serif text-2xl font-semibold">{busy ? "Filing your documents…" : "Add documents to the library"}</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Drag files here, or <span className="font-medium text-primary underline underline-offset-4">browse</span>
        </p>
      </div>
      {summary && <p className="text-xs text-muted-foreground/80">{summary}</p>}
    </div>
  );
}
