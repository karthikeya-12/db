"""Registry of provider fetchers, keyed by Nango provider key."""
from __future__ import annotations

from ingestion_pipeline.connectors.providers.base import ProviderFetcher


def _fetcher_classes() -> dict:
    from ingestion_pipeline.connectors.providers.atlassian import ConfluenceFetcher, JiraFetcher
    from ingestion_pipeline.connectors.providers.dropbox import DropboxFetcher
    from ingestion_pipeline.connectors.providers.github import GitHubFetcher
    from ingestion_pipeline.connectors.providers.gmail import GmailFetcher
    from ingestion_pipeline.connectors.providers.google_calendar import GoogleCalendarFetcher
    from ingestion_pipeline.connectors.providers.google_drive import GoogleDriveFetcher
    from ingestion_pipeline.connectors.providers.notion import NotionFetcher
    from ingestion_pipeline.connectors.providers.onedrive import OneDriveFetcher, OneDrivePersonalFetcher
    from ingestion_pipeline.connectors.providers.outlook import OutlookFetcher
    from ingestion_pipeline.connectors.providers.slack import SlackFetcher

    classes = (
        GmailFetcher, GoogleCalendarFetcher, GoogleDriveFetcher, NotionFetcher, SlackFetcher,
        OutlookFetcher, OneDriveFetcher, OneDrivePersonalFetcher, DropboxFetcher, GitHubFetcher, JiraFetcher, ConfluenceFetcher,
    )
    return {cls.provider: cls for cls in classes}


def get_fetcher(provider: str, http=None) -> ProviderFetcher:
    fetchers = _fetcher_classes()
    if provider not in fetchers:
        raise KeyError(f"No fetcher for provider {provider!r}. Supported: {sorted(fetchers)}")
    return fetchers[provider](http)


def supported_providers() -> list[str]:
    return sorted(_fetcher_classes())
