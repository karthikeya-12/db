"""Atlassian Cloud: Jira issues and Confluence pages, across every site the account can reach."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ingestion_pipeline.connectors.providers.base import ChangeRef, FetchedDocument, ProviderFetcher, html_to_text, safe_title
from ingestion_pipeline.core.types import ContentType

GATEWAY = "https://api.atlassian.com"


def normalize_ts(value: str) -> str:
    """Atlassian timestamps come as '...+0000' or '...Z'; store one comparable UTC form."""
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.replace("Z", "+00:00"))
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def adf_to_text(node) -> str:
    """Flatten Atlassian Document Format (Jira's rich text) into plain text."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    inner = "".join(adf_to_text(child) for child in node.get("content", []))
    if kind in {"paragraph", "heading", "codeBlock", "blockquote"}:
        return inner + "\n\n"
    if kind == "listItem":
        return "- " + inner.strip() + "\n"
    return inner


class _AtlassianFetcher(ProviderFetcher):
    def sites(self, token: str) -> list[dict]:
        return self.get_json(f"{GATEWAY}/oauth/token/accessible-resources", token)

    def _since(self, cursor, initial_days) -> datetime:
        if cursor:
            return datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - timedelta(days=initial_days)


class JiraFetcher(_AtlassianFetcher):
    provider = "jira"

    def list_changes(self, token, cursor, limit, initial_days):
        # JQL only has minute resolution and uses the account's time zone: ask for a day of slack, filter exactly here.
        since = self._since(cursor, initial_days) - timedelta(days=1)
        jql = f'updated >= "{since.strftime("%Y-%m-%d %H:%M")}" ORDER BY updated ASC'
        refs: list[ChangeRef] = []
        for site in self.sites(token):
            body = {"jql": jql, "maxResults": min(limit, 100), "fields": ["summary", "updated"]}
            data = self.request("POST", f"{GATEWAY}/ex/jira/{site['id']}/rest/api/3/search/jql", token, json=body).json()
            for issue in data.get("issues", []):
                updated = normalize_ts(issue["fields"]["updated"])
                if cursor and updated <= cursor:
                    continue
                title = f"{issue['key']} {issue['fields'].get('summary', '')}".strip()
                refs.append(ChangeRef(f"{site['id']}:{issue['key']}", title, updated, extra={"cloud": site["id"], "key": issue["key"], "site": site.get("url", "")}))
        refs.sort(key=lambda r: r.cursor)
        return refs[:limit]

    def fetch(self, token, ref):
        cloud, key = ref.extra["cloud"], ref.extra["key"]
        issue = self.get_json(f"{GATEWAY}/ex/jira/{cloud}/rest/api/3/issue/{key}", token, params={"fields": "summary,description,status,assignee,reporter,priority,comment,labels"})
        f = issue["fields"]
        lines = [
            f"# {ref.title}",
            "",
            f"Status: {(f.get('status') or {}).get('name', '')}",
            f"Assignee: {(f.get('assignee') or {}).get('displayName', 'Unassigned')}",
            f"Reporter: {(f.get('reporter') or {}).get('displayName', '')}",
            f"URL: {ref.extra.get('site', '')}/browse/{key}",
            "",
            adf_to_text(f.get("description")).strip(),
        ]
        for c in (f.get("comment") or {}).get("comments", []):
            lines += ["", f"## {(c.get('author') or {}).get('displayName', '')} · {c.get('created', '')}", "", adf_to_text(c.get("body")).strip()]
        return FetchedDocument(
            file_name=f"{safe_title(ref.title)}.md",
            content_type=ContentType.DOCUMENT,
            raw_bytes="\n".join(lines).encode("utf-8"),
            metadata={"provider": self.provider, "external_id": ref.external_id, "title": ref.title, "updated": ref.cursor},
        )


class ConfluenceFetcher(_AtlassianFetcher):
    provider = "confluence"

    def list_changes(self, token, cursor, limit, initial_days):
        since = self._since(cursor, initial_days) - timedelta(days=1)
        cql = f'type = page AND lastmodified >= "{since.strftime("%Y-%m-%d %H:%M")}" ORDER BY lastmodified ASC'
        refs: list[ChangeRef] = []
        for site in self.sites(token):
            data = self.get_json(
                f"{GATEWAY}/ex/confluence/{site['id']}/wiki/rest/api/content/search",
                token,
                params={"cql": cql, "limit": min(limit, 100), "expand": "version"},
            )
            for page in data.get("results", []):
                updated = normalize_ts(page["version"]["when"])
                if cursor and updated <= cursor:
                    continue
                refs.append(ChangeRef(f"{site['id']}:{page['id']}", page.get("title", "Untitled"), updated, extra={"cloud": site["id"], "page": page["id"]}))
        refs.sort(key=lambda r: r.cursor)
        return refs[:limit]

    def fetch(self, token, ref):
        page = self.get_json(
            f"{GATEWAY}/ex/confluence/{ref.extra['cloud']}/wiki/rest/api/content/{ref.extra['page']}",
            token,
            params={"expand": "body.storage,version"},
        )
        text = html_to_text(page["body"]["storage"]["value"])
        return FetchedDocument(
            file_name=f"{safe_title(ref.title)}.md",
            content_type=ContentType.DOCUMENT,
            raw_bytes=f"# {ref.title}\n\n{text}\n".encode("utf-8"),
            metadata={"provider": self.provider, "external_id": ref.external_id, "title": ref.title, "updated": ref.cursor},
        )
