"""Dropbox: readable files found by walking the account's folders."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ingestion_pipeline.config import get_settings
from ingestion_pipeline.connectors.providers.base import ChangeRef, FetchedDocument, ProviderFetcher
from ingestion_pipeline.core.formats import classify

API = "https://api.dropboxapi.com/2/files"
CONTENT = "https://content.dropboxapi.com/2/files/download"
MAX_PAGES = 30


class DropboxFetcher(ProviderFetcher):
    provider = "dropbox"

    def list_changes(self, token, cursor, limit, initial_days):
        floor = cursor or (datetime.now(timezone.utc) - timedelta(days=initial_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        max_bytes = get_settings().upload_max_mb * 1024 * 1024
        refs: list[ChangeRef] = []
        data = self.request("POST", f"{API}/list_folder", token, json={"path": "", "recursive": True, "limit": 2000}).json()
        for _ in range(MAX_PAGES):
            for entry in data.get("entries", []):
                modified, name = entry.get("server_modified", ""), entry.get("name", "")
                if entry.get(".tag") != "file" or modified <= floor or classify(name) is None or entry.get("size", 0) > max_bytes:
                    continue
                refs.append(ChangeRef(entry["id"], entry.get("path_display", name), modified, extra={"file_name": name}))
            if not data.get("has_more"):
                break
            data = self.request("POST", f"{API}/list_folder/continue", token, json={"cursor": data["cursor"]}).json()
        refs.sort(key=lambda r: r.cursor)
        return refs[:limit]

    def fetch(self, token, ref):
        response = self.request("POST", CONTENT, token, headers={"Dropbox-API-Arg": json.dumps({"path": ref.external_id})})
        file_name = ref.extra["file_name"]
        return FetchedDocument(
            file_name=file_name,
            content_type=classify(file_name),
            raw_bytes=response.content,
            metadata={"provider": self.provider, "external_id": ref.external_id, "title": ref.title, "modified": ref.cursor},
        )
