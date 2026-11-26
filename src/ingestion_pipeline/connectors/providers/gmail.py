"""Gmail: each message becomes a Markdown document (headers + text body)."""
from __future__ import annotations

import base64
import time
from datetime import datetime, timezone

from ingestion_pipeline.connectors.providers.base import (
    ChangeRef,
    FetchedDocument,
    ProviderFetcher,
    html_to_text,
    safe_title,
)
from ingestion_pipeline.core.types import ContentType

API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_PAGES = 40  # of 100 ids each


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _headers(payload: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def _collect(part: dict, plain: list[str], html: list[str], attachments: list[str]) -> None:
    mime = part.get("mimeType", "")
    body = part.get("body", {})
    if part.get("filename") and body.get("attachmentId"):
        attachments.append(part["filename"])
    elif mime == "text/plain" and body.get("data"):
        plain.append(_b64(body["data"]))
    elif mime == "text/html" and body.get("data"):
        html.append(_b64(body["data"]))
    for child in part.get("parts", []) or []:
        _collect(child, plain, html, attachments)


class GmailFetcher(ProviderFetcher):
    provider = "google-mail"

    def list_changes(self, token, cursor, limit, initial_days):
        cursor_ms = int(cursor) if cursor else 0
        query = f"after:{cursor_ms // 1000}" if cursor_ms else f"newer_than:{initial_days}d"

        ids: list[str] = []
        page_token = None
        for _ in range(MAX_PAGES):
            params = {"q": query, "maxResults": 100, **({"pageToken": page_token} if page_token else {})}
            data = self.get_json(f"{API}/messages", token, params=params)
            ids.extend(m["id"] for m in data.get("messages", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        refs: list[ChangeRef] = []
        for message_id in reversed(ids):  # the API lists newest first; take the oldest candidates first
            meta = self.get_json(
                f"{API}/messages/{message_id}", token, params={"format": "metadata", "metadataHeaders": "Subject"}
            )
            internal = int(meta.get("internalDate", 0))
            if internal <= cursor_ms:
                continue
            refs.append(ChangeRef(message_id, _headers(meta.get("payload", {})).get("subject", "(no subject)"), str(internal)))
        refs.sort(key=lambda r: int(r.cursor))
        return refs[:limit]

    def list_latest(self, token, limit, initial_days, skip_ids):
        """Newest `limit` messages of the last `initial_days` that were not ingested yet (newest first from the API)."""
        picked: list[str] = []
        page_token = None
        for _ in range(MAX_PAGES):
            params = {"q": f"newer_than:{initial_days}d", "maxResults": 100, **({"pageToken": page_token} if page_token else {})}
            data = self.get_json(f"{API}/messages", token, params=params)
            picked.extend(m["id"] for m in data.get("messages", []) if m["id"] not in skip_ids)
            page_token = data.get("nextPageToken")
            if len(picked) >= limit or not page_token:
                break
        refs = []
        for message_id in picked[:limit]:
            meta = self.get_json(f"{API}/messages/{message_id}", token, params={"format": "metadata", "metadataHeaders": "Subject"})
            refs.append(ChangeRef(message_id, _headers(meta.get("payload", {})).get("subject", "(no subject)"), str(int(meta.get("internalDate", 0)))))
        refs.sort(key=lambda r: int(r.cursor))
        return refs

    def fetch(self, token, ref):
        message = self.get_json(f"{API}/messages/{ref.external_id}", token, params={"format": "full"})
        payload = message.get("payload", {})
        headers = _headers(payload)
        plain: list[str] = []
        html: list[str] = []
        attachments: list[str] = []
        _collect(payload, plain, html, attachments)
        body = "\n\n".join(plain).strip() or html_to_text("\n".join(html)) or message.get("snippet", "")

        subject = headers.get("subject", "(no subject)")
        sent = datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, tz=timezone.utc).isoformat()
        lines = [f"# {subject}", "", f"- From: {headers.get('from', '')}", f"- To: {headers.get('to', '')}"]
        if headers.get("cc"):
            lines.append(f"- Cc: {headers['cc']}")
        lines += [f"- Date: {sent}", ""]
        if attachments:
            lines += ["Attachments (not ingested): " + ", ".join(attachments), ""]
        lines.append(body)
        return FetchedDocument(
            file_name=f"{safe_title(subject, 'Email')}.md",
            content_type=ContentType.MESSAGE,
            raw_bytes="\n".join(lines).encode("utf-8"),
            metadata={"provider": self.provider, "external_id": ref.external_id, "title": subject, "sent_at": sent},
        )
