"""Slack: one Markdown document per channel per day, always rebuilt from the whole day."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ingestion_pipeline.connectors.providers.base import ChangeRef, FetchedDocument, ProviderError, ProviderFetcher
from ingestion_pipeline.core.types import ContentType

API = "https://slack.com/api"
_SYSTEM_SUBTYPES = {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name", "bot_add", "pinned_item"}


def _day_bounds(day: str) -> tuple[float, float]:
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


class SlackFetcher(ProviderFetcher):
    provider = "slack"

    def _call(self, method: str, token: str, **params) -> dict:
        data = self.get_json(f"{API}/{method}", token, params={k: v for k, v in params.items() if v is not None})
        if not data.get("ok"):
            raise ProviderError(f"slack {method}: {data.get('error', 'unknown_error')}")
        return data

    def _channels(self, token: str) -> list[dict]:
        channels, cursor = [], None
        while True:
            data = self._call("conversations.list", token, types="public_channel", exclude_archived="true", limit=200, cursor=cursor)
            channels += [c for c in data.get("channels", []) if c.get("is_member")]
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return channels

    def _history(self, token: str, channel: str, oldest: float, latest: float | None = None) -> list[dict]:
        messages, cursor = [], None
        while True:
            data = self._call(
                "conversations.history", token, channel=channel, oldest=f"{oldest:.6f}",
                latest=f"{latest:.6f}" if latest else None, limit=200, cursor=cursor, inclusive="false",
            )
            messages += data.get("messages", [])
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not (data.get("has_more") and cursor):
                return messages

    def _users(self, token: str) -> dict[str, str]:
        names, cursor = {}, None
        while True:
            data = self._call("users.list", token, limit=200, cursor=cursor)
            for member in data.get("members", []):
                names[member["id"]] = member.get("real_name") or member.get("name") or member["id"]
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return names

    def list_changes(self, token, cursor, limit, initial_days):
        oldest = float(cursor) if cursor else (datetime.now(timezone.utc) - timedelta(days=initial_days)).timestamp()
        groups: dict[tuple[str, str], dict] = {}
        for channel in self._channels(token):
            for message in self._history(token, channel["id"], oldest):
                if message.get("subtype") in _SYSTEM_SUBTYPES:
                    continue
                ts = float(message["ts"])
                day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                group = groups.setdefault((channel["id"], day), {"name": channel.get("name", channel["id"]), "max_ts": ts})
                group["max_ts"] = max(group["max_ts"], ts)
        refs = [
            ChangeRef(f"{channel_id}/{day}", f"#{info['name']} {day}", f"{info['max_ts']:.6f}", extra={"channel": channel_id, "name": info["name"], "day": day})
            for (channel_id, day), info in groups.items()
        ]
        refs.sort(key=lambda r: float(r.cursor))
        return refs[:limit]

    def fetch(self, token, ref):
        start, end = _day_bounds(ref.extra["day"])
        messages = sorted(self._history(token, ref.extra["channel"], start - 0.000001, end), key=lambda m: float(m["ts"]))
        users = self._users(token)
        lines = [f"# #{ref.extra['name']} — {ref.extra['day']}", ""]
        for message in messages:
            if message.get("subtype") in _SYSTEM_SUBTYPES:
                continue
            when = datetime.fromtimestamp(float(message["ts"]), tz=timezone.utc).strftime("%H:%M")
            who = users.get(message.get("user", ""), message.get("username") or message.get("user") or "unknown")
            lines.append(f"**{who}** ({when} UTC): {message.get('text', '')}")
            lines.append("")
        return FetchedDocument(
            file_name=f"{ref.title}.md",
            content_type=ContentType.TRANSCRIPT,
            raw_bytes="\n".join(lines).encode("utf-8"),
            metadata={"provider": self.provider, "external_id": ref.external_id, "title": ref.title, "channel": ref.extra["name"], "day": ref.extra["day"]},
        )
