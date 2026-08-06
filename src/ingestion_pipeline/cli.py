"""
Local run entrypoint: `python -m ingestion_pipeline.cli`

Wires the demo LocalFolderAdapter into the orchestrator so the whole
pipeline (dedup, archive, chunk, embed, persist, publish) can be exercised
against real files in data/incoming/ without any cloud dependency. Swapping
in the real S3 adapter later is a one-line change here -- nothing else in
this file, or in orchestrator.py, needs to change.
"""
from __future__ import annotations

import logging

from ingestion_pipeline.adapters.local_folder_adapter import LocalFolderAdapter
from ingestion_pipeline.config import get_settings
from ingestion_pipeline.core.blobstore import get_blob_store
from ingestion_pipeline.core.embedder import get_embedder
from ingestion_pipeline.core.logging_config import configure_logging
from ingestion_pipeline.core.orchestrator import PipelineOrchestrator


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    adapter = LocalFolderAdapter("./data/incoming")
    orchestrator = PipelineOrchestrator(
        adapter=adapter,
        blob_store=get_blob_store(),
        embedder=get_embedder(),
    )
    summary = orchestrator.run()
    logging.getLogger(__name__).info("run complete: %s", summary)


if __name__ == "__main__":
    main()
