export type ItemStatus = "NEW" | "PROCESSING" | "DONE" | "FAILED" | "DUPLICATE";

export interface Item {
  item_id: string;
  source_id: string;
  content_type: string;
  file_name?: string | null;
  status: ItemStatus;
  error_reason: string | null;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
}

export interface UploadFormats {
  extensions: string[];
  max_mb: number;
}

export interface UploadResult {
  file_name: string;
  item_id?: string;
  size_bytes?: number;
  status: "queued" | "rejected";
  error?: string;
}

export interface Run {
  run_id: string;
  source_id: string;
  items_identified: number;
  items_processed: number;
  items_failed: number;
  items_skipped_duplicate: number;
  items_dead_lettered: number;
  started_at: string | null;
  duration_seconds: number | null;
}

export interface Job {
  job_id: string;
  source_id: string;
  kind: string;
  status: string;
  attempts: number;
  error: string | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface DeadLetter {
  id: string;
  item_id: string;
  source_id: string;
  stage_failed: string;
  error_message: string;
  attempt_count: number;
  failed_at: string | null;
  requeued_at: string | null;
}

export interface ConnectionInfo {
  id: string;
  integration_key: string;
  provider: string;
  display_name: string;
  status: "ACTIVE" | "ERROR" | "DISCONNECTED";
  enabled: boolean;
  last_synced_at: string | null;
  last_error: string | null;
  source_id: string;
  created_at: string | null;
}

export interface CatalogProvider {
  provider: string;
  name: string;
  category: string;
  description: string;
  supported: boolean;
  integration_key: string | null;
  connections: ConnectionInfo[];
}

export interface CatalogResponse {
  nango: { ok: boolean; error: string | null };
  categories: string[];
  providers: CatalogProvider[];
}

export interface ConnectionDetail {
  name: string;
  connection: ConnectionInfo;
  stats: { documents: number; failed: number; in_progress: number };
  recent_items: { item_id: string; file_name: string | null; status: ItemStatus; updated_at: string | null }[];
  recent_runs: {
    run_id: string;
    items_identified: number;
    items_processed: number;
    items_failed: number;
    started_at: string | null;
    duration_seconds: number | null;
  }[];
}
