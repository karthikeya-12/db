"""GitHub: issues and pull requests (with their discussion) from every repository the user can see."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ingestion_pipeline.connectors.providers.base import ChangeRef, FetchedDocument, ProviderFetcher, safe_title
from ingestion_pipeline.core.types import ContentType

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


class GitHubFetcher(ProviderFetcher):
    provider = "github"

    def list_changes(self, token, cursor, limit, initial_days):
        since = cursor or (datetime.now(timezone.utc) - timedelta(days=initial_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        refs: list[ChangeRef] = []
        page = 1
        while len(refs) < limit:
            params = {"filter": "all", "state": "all", "since": since, "sort": "updated", "direction": "asc", "per_page": 100, "page": page}
            items = self.request("GET", f"{API}/issues", token, params=params, headers=HEADERS).json()
            for issue in items:
                updated = issue.get("updated_at", "")
                if cursor and updated <= cursor:
                    continue
                repo = (issue.get("repository") or {}).get("full_name", "")
                title = f"{repo}#{issue['number']} {issue.get('title', '')}".strip()
                refs.append(ChangeRef(str(issue["id"]), title, updated, extra={"url": issue["url"], "comments_url": issue.get("comments_url")}))
            if len(items) < 100:
                break
            page += 1
        return refs[:limit]

    def fetch(self, token, ref):
        issue = self.get_json(ref.extra["url"], token, headers=HEADERS)
        kind = "Pull request" if issue.get("pull_request") else "Issue"
        lines = [
            f"# {ref.title}",
            "",
            f"{kind} · {issue.get('state', '')} · by {(issue.get('user') or {}).get('login', '')}",
            f"Labels: {', '.join(l['name'] for l in issue.get('labels', []))}" if issue.get("labels") else "",
            f"URL: {issue.get('html_url', '')}",
            "",
            (issue.get("body") or "").strip(),
        ]
        if issue.get("comments") and ref.extra.get("comments_url"):
            comments = self.request("GET", ref.extra["comments_url"], token, params={"per_page": 100}, headers=HEADERS).json()
            for c in comments:
                lines += ["", f"## {(c.get('user') or {}).get('login', '')} · {c.get('created_at', '')}", "", (c.get("body") or "").strip()]
        return FetchedDocument(
            file_name=f"{safe_title(ref.title)}.md",
            content_type=ContentType.DOCUMENT,
            raw_bytes="\n".join(lines).encode("utf-8"),
            metadata={"provider": self.provider, "external_id": ref.external_id, "title": ref.title, "updated": ref.cursor},
        )
