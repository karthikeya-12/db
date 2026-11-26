"""Google Drive: Docs/Sheets/Slides are exported to Office formats; other files are downloaded when we can read them."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ingestion_pipeline.config import get_settings
from ingestion_pipeline.connectors.providers.base import ChangeRef, FetchedDocument, ProviderFetcher, safe_title
from ingestion_pipeline.core.formats import classify

API = "https://www.googleapis.com/drive/v3/files"

# Google-native types -> (export mime type, extension)
EXPORTS = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}


class GoogleDriveFetcher(ProviderFetcher):
    provider = "google-drive"

    def _target_name(self, item: dict) -> str | None:
        """The file name we would ingest it as, or None to skip (folders, unsupported formats, too large)."""
        mime = item.get("mimeType", "")
        name = item.get("name", "Untitled")
        if mime in EXPORTS:
            return f"{safe_title(name)}{EXPORTS[mime][1]}"
        if mime.startswith("application/vnd.google-apps."):
            return None  # folders, forms, shortcuts, ...
        if classify(name) is None:
            return None
        if int(item.get("size", 0) or 0) > get_settings().upload_max_mb * 1024 * 1024:
            return None
        return name

    def list_changes(self, token, cursor, limit, initial_days):
        since = cursor or (datetime.now(timezone.utc) - timedelta(days=initial_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        refs: list[ChangeRef] = []
        page_token = None
        while len(refs) < limit:
            params = {
                "q": f"modifiedTime > '{since}' and trashed = false and mimeType != 'application/vnd.google-apps.folder'",
                "orderBy": "modifiedTime",
                "pageSize": 100,
                "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                **({"pageToken": page_token} if page_token else {}),
            }
            data = self.get_json(API, token, params=params)
            for item in data.get("files", []):
                target = self._target_name(item)
                modified = item.get("modifiedTime", "")
                if target is None or (cursor and modified <= cursor):
                    continue
                refs.append(ChangeRef(item["id"], item.get("name", target), modified, extra={"mime": item["mimeType"], "file_name": target}))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        refs.sort(key=lambda r: r.cursor)
        return refs[:limit]

    def fetch(self, token, ref):
        mime = ref.extra["mime"]
        if mime in EXPORTS:
            response = self.request("GET", f"{API}/{ref.external_id}/export", token, params={"mimeType": EXPORTS[mime][0]})
        else:
            response = self.request("GET", f"{API}/{ref.external_id}", token, params={"alt": "media"})
        file_name = ref.extra["file_name"]
        content_type = classify(file_name)
        return FetchedDocument(
            file_name=file_name,
            content_type=content_type,
            raw_bytes=response.content,
            metadata={"provider": self.provider, "external_id": ref.external_id, "title": ref.title, "modified": ref.cursor},
        )
