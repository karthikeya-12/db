import type { Item } from "@/lib/types";

/** "upload:Q3 report.pdf" -> "Q3 report.pdf"; "nango:gmail:abc/Invoice" -> "Invoice". */
export function displayName(item: Item): string {
  if (item.file_name) return item.file_name;
  const afterPrefix = item.item_id.includes(":") ? item.item_id.slice(item.item_id.indexOf(":") + 1) : item.item_id;
  return afterPrefix.split("/").pop() || afterPrefix;
}

const PROVIDER_NAMES: Record<string, string> = {
  "google-mail": "Gmail",
  "google-drive": "Google Drive",
  "google-calendar": "Google Calendar",
  slack: "Slack",
  notion: "Notion",
  outlook: "Outlook",
  "one-drive": "OneDrive",
  dropbox: "Dropbox",
  github: "GitHub",
  confluence: "Confluence",
  jira: "Jira",
};

export function sourceLabel(sourceId: string): string {
  if (sourceId === "upload") return "Upload";
  if (sourceId === "local_folder") return "Local folder";
  if (sourceId.startsWith("nango:")) {
    const key = sourceId.split(":")[1];
    return PROVIDER_NAMES[key] ?? key.replace(/-/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  }
  return sourceId;
}

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toUpperCase() : "FILE";
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
];

export function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
  if (Math.abs(seconds) < 45) return "just now";
  const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return formatter.format(Math.round(seconds / size), unit);
  }
  return formatter.format(Math.round(seconds / 60), "minute");
}

export function itemNameFromId(itemId: string): string {
  const afterPrefix = itemId.includes(":") ? itemId.slice(itemId.indexOf(":") + 1) : itemId;
  return afterPrefix.split("/").pop() || afterPrefix;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

const STAGE_LABELS: Record<string, string> = {
  "normalize-persist": "Reading the document",
  "fetch": "Fetching",
  "archive": "Archiving",
  "cognee-index": "Indexing",
  "dedup": "Checking duplicates",
};

export function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage;
}
