"""OneDrive for Business / personal (Microsoft Graph): readable files from the drive."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ingestion_pipeline.config import get_settings
from ingestion_pipeline.connectors.providers.base import ChangeRef, FetchedDocument, ProviderError, ProviderFetcher
from ingestion_pipeline.core.formats import classify

ROOT = "https://graph.microsoft.com/v1.0/me/drive"
MAX_PAGES = 30


class OneDriveFetcher(ProviderFetcher):
    provider = "one-drive"

    def list_changes(self, token, cursor, limit, initial_days):
        floor = cursor or (datetime.now(timezone.utc) - timedelta(days=initial_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        max_bytes = get_settings().upload_max_mb * 1024 * 1024
        refs: list[ChangeRef] = []
        url, params = f"{ROOT}/root/delta", {"$top": 200}
        for _ in range(MAX_PAGES):
            data = self.get_json(url, token, params=params)
            for item in data.get("value", []):
                name, modified = item.get("name", ""), item.get("lastModifiedDateTime", "")
                if "file" not in item or "deleted" in item or modified <= floor or classify(name) is None:
                    continue
                if int(item.get("size", 0) or 0) > max_bytes:
                    continue
                refs.append(ChangeRef(item["id"], name, modified, extra={"file_name": name}))
            url, params = data.get("@odata.nextLink"), None
            if not url:
                break
        refs.sort(key=lambda r: r.cursor)
        return refs[:limit]

    def fetch(self, token, ref):
        meta = self.get_json(f"{ROOT}/items/{ref.external_id}", token)
        download = meta.get("@microsoft.graph.downloadUrl")
        if not download:
            raise ProviderError(f"{self.provider}: no download link for {ref.title}")
        response = self._http.get(download)  # pre-authenticated link: must not carry our bearer token
        if response.status_code >= 400:
            raise ProviderError(f"{self.provider}: download answered {response.status_code}")
        file_name = ref.extra["file_name"]
        return FetchedDocument(
            file_name=file_name,
            content_type=classify(file_name),
            raw_bytes=response.content,
            metadata={"provider": self.provider, "external_id": ref.external_id, "title": ref.title, "modified": ref.cursor},
        )


class OneDrivePersonalFetcher(OneDriveFetcher):
    provider = "one-drive-personal"
