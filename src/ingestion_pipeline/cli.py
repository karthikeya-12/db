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
from ingestion_pipeline.core.logging_config import configure_logging
from ingestion_pipeline.core.orchestrator import PipelineOrchestrator


def create_user_command(email: str) -> None:
    """`python -m ingestion_pipeline.cli create-user you@company.com` -- prompts for the password."""
    import getpass

    from ingestion_pipeline.core.auth import AuthError, create_user
    from ingestion_pipeline.db.session import ensure_schema

    ensure_schema()
    password = getpass.getpass("Password (min 10 characters): ")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("Passwords do not match.")
    try:
        user = create_user(email, password)
    except AuthError as exc:
        raise SystemExit(str(exc))
    print(f"Created user {user['email']}")


def reset_password_command(email: str) -> None:
    import getpass

    from ingestion_pipeline.core.auth import AuthError, set_password

    password = getpass.getpass("New password (min 10 characters): ")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("Passwords do not match.")
    try:
        found = set_password(email, password)
    except AuthError as exc:
        raise SystemExit(str(exc))
    print("Password updated." if found else f"No user with email {email}.")


def main() -> None:
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "create-user":
        return create_user_command(sys.argv[2])
    if len(sys.argv) >= 3 and sys.argv[1] == "reset-password":
        return reset_password_command(sys.argv[2])
    settings = get_settings()
    configure_logging(settings.log_level)

    adapter = LocalFolderAdapter("./data/incoming")
    orchestrator = PipelineOrchestrator(
        adapter=adapter,
        blob_store=get_blob_store(),
    )
    summary = orchestrator.run()
    logging.getLogger(__name__).info("run complete: %s", summary)


if __name__ == "__main__":
    main()
