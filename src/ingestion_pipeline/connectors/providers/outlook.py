"""Outlook (Microsoft Graph): mail messages rendered as Markdown."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ingestion_pipeline.connectors.providers.base import ChangeRef, FetchedDocument, ProviderFetcher, html_to_text, safe_title
from ingestion_pipeline.core.types import ContentType

API = "https://graph.microsoft.com/v1.0/me/messages"


def _addresses(items: list[dict] | None) -> str:
    return ", ".join((i.get("emailAddress") or {}).get("address", "") for i in items or [])


class OutlookFetcher(ProviderFetcher):
    provider = "outlook"

    def list_changes(self, token, cursor, limit, initial_days):
        since = cursor or (datetime.now(timezone.utc) - timedelta(days=initial_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "$filter": f"receivedDateTime gt {since}",
            "$orderby": "receivedDateTime asc",
            "$select": "id,subject,receivedDateTime",
            "$top": min(limit, 100),
        }
        refs: list[ChangeRef] = []
        url, query = API, params
        while url and len(refs) < limit:
            data = self.get_json(url, token, params=query)
            for m in data.get("value", []):
                received = m.get("receivedDateTime", "")
                if cursor and received <= cursor:
                    continue
                refs.append(ChangeRef(m["id"], m.get("subject") or "(no subject)", received))
            url, query = data.get("@odata.nextLink"), None  # nextLink already carries the query
        return refs[:limit]

    def fetch(self, token, ref):
        m = self.request(
            "GET", f"{API}/{ref.external_id}", token, headers={"Prefer": 'outlook.body-content-type="html"'}
        ).json()
        body = m.get("body") or {}
        text = html_to_text(body.get("content", "")) if body.get("contentType", "").lower() == "html" else body.get("content", "")
        subject = m.get("subject") or "(no subject)"
        lines = [
            f"# {subject}",
            "",
            f"From: {_addresses([m.get('from')] if m.get('from') else [])}",
            f"To: {_addresses(m.get('toRecipients'))}",
            *([f"Cc: {_addresses(m.get('ccRecipients'))}"] if m.get("ccRecipients") else []),
            f"Date: {m.get('receivedDateTime', '')}",
            "",
            text.strip(),
        ]
        if m.get("hasAttachments"):
            lines += ["", "(This message has attachments that were not ingested.)"]
        return FetchedDocument(
            file_name=f"{safe_title(subject)}.md",
            content_type=ContentType.MESSAGE,
            raw_bytes="\n".join(lines).encode("utf-8"),
            metadata={"provider": self.provider, "external_id": ref.external_id, "title": subject, "received": ref.cursor},
        )
