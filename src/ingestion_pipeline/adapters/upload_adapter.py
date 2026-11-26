"""
Documents uploaded through the API. The API saves each file into the uploads folder and queues a
scan of source "upload"; this adapter then reads that folder exactly like the local-folder adapter
(per-file content hashing means unchanged files are skipped and a re-uploaded file with new content
becomes a new version of the same document).
"""
from __future__ import annotations

from ingestion_pipeline.adapters.local_folder_adapter import LocalFolderAdapter


class UploadAdapter(LocalFolderAdapter):
    source_id = "upload"
    item_prefix = "upload"
