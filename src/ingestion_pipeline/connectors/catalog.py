"""The connectors we offer. `provider` is Nango's provider key; `supported` means we have a fetcher for it."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CatalogEntry:
    provider: str
    name: str
    category: str
    description: str
    supported: bool = True


CATEGORY_ORDER = ["Email & Calendar", "Files & Storage", "Messaging", "Knowledge & Docs"]

CATALOG: list[CatalogEntry] = [
    CatalogEntry("google-mail", "Gmail", "Email & Calendar", "Emails from the last 30 days, then every new message."),
    CatalogEntry("google-calendar", "Google Calendar", "Email & Calendar", "Events with their attendees and descriptions."),
    CatalogEntry("google-drive", "Google Drive", "Files & Storage", "Docs, Sheets, Slides, PDFs and text files."),
    CatalogEntry("slack", "Slack", "Messaging", "Public channels you have added the app to, one record per channel per day."),
    CatalogEntry("notion", "Notion", "Knowledge & Docs", "Pages shared with the integration, with their full content."),
    CatalogEntry("outlook", "Outlook", "Email & Calendar", "Mail from the last 30 days, then every new message."),
    CatalogEntry("one-drive", "OneDrive for Business", "Files & Storage", "Office documents, PDFs and text files from your drive."),
    CatalogEntry("one-drive-personal", "OneDrive Personal", "Files & Storage", "Office documents, PDFs and text files from your personal drive."),
    CatalogEntry("dropbox", "Dropbox", "Files & Storage", "Documents, PDFs and text files anywhere in your Dropbox."),
    CatalogEntry("github", "GitHub", "Knowledge & Docs", "Issues and pull requests, with their discussions."),
    CatalogEntry("jira", "Jira", "Knowledge & Docs", "Issues with descriptions and comments."),
    CatalogEntry("confluence", "Confluence", "Knowledge & Docs", "Wiki pages with their full text."),
    CatalogEntry("sharepoint-online", "SharePoint", "Files & Storage", "Coming soon.", supported=False),
    CatalogEntry("microsoft-teams", "Microsoft Teams", "Messaging", "Coming soon.", supported=False),
    CatalogEntry("hubspot", "HubSpot", "Knowledge & Docs", "Coming soon.", supported=False),
    CatalogEntry("zoom", "Zoom", "Messaging", "Coming soon.", supported=False),
]


def catalog_dicts() -> list[dict]:
    return [asdict(entry) for entry in CATALOG]
