"""Google Calendar: each event becomes a short Markdown document."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ingestion_pipeline.connectors.providers.base import ChangeRef, FetchedDocument, ProviderFetcher, safe_title
from ingestion_pipeline.core.types import ContentType

API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def _when(point: dict) -> str:
    return point.get("dateTime") or point.get("date") or ""


class GoogleCalendarFetcher(ProviderFetcher):
    provider = "google-calendar"

    def list_changes(self, token, cursor, limit, initial_days):
        updated_min = cursor or (datetime.now(timezone.utc) - timedelta(days=initial_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        refs: list[ChangeRef] = []
        page_token = None
        while len(refs) < limit:
            params = {
                "updatedMin": updated_min,
                "singleEvents": "true",
                "orderBy": "updated",
                "maxResults": 250,
                "showDeleted": "false",
                **({"pageToken": page_token} if page_token else {}),
            }
            data = self.get_json(API, token, params=params)
            for event in data.get("items", []):
                if event.get("status") == "cancelled":
                    continue
                updated = event.get("updated", "")
                if cursor and updated <= cursor:
                    continue
                refs.append(ChangeRef(event["id"], event.get("summary", "(untitled event)"), updated, extra={"event": event}))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        refs.sort(key=lambda r: r.cursor)
        return refs[:limit]

    def fetch(self, token, ref):
        event = ref.extra["event"]
        attendees = ", ".join(a.get("email", "") for a in event.get("attendees", []) or [])
        lines = [
            f"# {event.get('summary', '(untitled event)')}",
            "",
            f"- Starts: {_when(event.get('start', {}))}",
            f"- Ends: {_when(event.get('end', {}))}",
        ]
        if event.get("location"):
            lines.append(f"- Location: {event['location']}")
        if event.get("organizer", {}).get("email"):
            lines.append(f"- Organizer: {event['organizer']['email']}")
        if attendees:
            lines.append(f"- Attendees: {attendees}")
        if event.get("description"):
            lines += ["", event["description"]]
        title = event.get("summary", "Event")
        return FetchedDocument(
            file_name=f"{safe_title(title, 'Event')}.md",
            content_type=ContentType.MESSAGE,
            raw_bytes="\n".join(lines).encode("utf-8"),
            metadata={"provider": self.provider, "external_id": ref.external_id, "title": title, "updated": ref.cursor},
        )
