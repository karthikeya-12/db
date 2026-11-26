"""Notion: each page becomes a Markdown document built from its blocks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ingestion_pipeline.connectors.providers.base import ChangeRef, FetchedDocument, ProviderFetcher, safe_title
from ingestion_pipeline.core.types import ContentType

API = "https://api.notion.com/v1"
HEADERS = {"Notion-Version": "2022-06-28"}
MAX_DEPTH = 3


def _rich(items: list[dict]) -> str:
    return "".join(item.get("plain_text", "") for item in items or [])


def page_title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return _rich(prop.get("title", [])) or "Untitled"
    return "Untitled"


def block_to_lines(block: dict, depth: int) -> list[str]:
    kind = block.get("type", "")
    data = block.get(kind, {}) if isinstance(block.get(kind), dict) else {}
    text = _rich(data.get("rich_text", []))
    indent = "  " * depth
    if kind == "heading_1":
        return [f"# {text}", ""]
    if kind == "heading_2":
        return [f"## {text}", ""]
    if kind == "heading_3":
        return [f"### {text}", ""]
    if kind == "bulleted_list_item":
        return [f"{indent}- {text}"]
    if kind == "numbered_list_item":
        return [f"{indent}1. {text}"]
    if kind == "to_do":
        return [f"{indent}- [{'x' if data.get('checked') else ' '}] {text}"]
    if kind == "quote":
        return [f"> {text}", ""]
    if kind == "code":
        return ["```", text, "```", ""]
    if kind == "callout":
        return [f"> {text}", ""]
    if kind == "toggle":
        return [f"{indent}- {text}"]
    if kind == "divider":
        return ["---", ""]
    if kind == "child_page":
        return [f"## {data.get('title', '')}", ""]
    if kind == "table_row":
        return ["| " + " | ".join(_rich(cell) for cell in data.get("cells", [])) + " |"]
    return [text, ""] if text else []


class NotionFetcher(ProviderFetcher):
    provider = "notion"

    def list_changes(self, token, cursor, limit, initial_days):
        floor = cursor or (datetime.now(timezone.utc) - timedelta(days=initial_days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        found: list[ChangeRef] = []
        start_cursor = None
        done = False
        while not done:
            body = {
                "filter": {"property": "object", "value": "page"},
                "sort": {"direction": "descending", "timestamp": "last_edited_time"},
                "page_size": 100,
                **({"start_cursor": start_cursor} if start_cursor else {}),
            }
            data = self.request("POST", f"{API}/search", token, json=body, headers=HEADERS).json()
            for page in data.get("results", []):
                edited = page.get("last_edited_time", "")
                if edited <= floor:
                    done = True  # results are newest first: everything after this is older
                    break
                if page.get("archived") or page.get("in_trash"):
                    continue
                found.append(ChangeRef(page["id"], page_title(page), edited))
            start_cursor = data.get("next_cursor")
            if not data.get("has_more") or not start_cursor:
                break
        found.sort(key=lambda r: r.cursor)
        return found[:limit]

    def _blocks(self, token: str, block_id: str, depth: int) -> list[str]:
        lines: list[str] = []
        start_cursor = None
        while True:
            params = {"page_size": 100, **({"start_cursor": start_cursor} if start_cursor else {})}
            data = self.get_json(f"{API}/blocks/{block_id}/children", token, params=params, headers=HEADERS)
            for block in data.get("results", []):
                lines += block_to_lines(block, depth)
                if block.get("has_children") and depth < MAX_DEPTH and block.get("type") != "child_page":
                    lines += self._blocks(token, block["id"], depth + 1)
            start_cursor = data.get("next_cursor")
            if not data.get("has_more") or not start_cursor:
                return lines

    def fetch(self, token, ref):
        body = "\n".join(self._blocks(token, ref.external_id, 0)).strip()
        text = f"# {ref.title}\n\n{body}\n"
        return FetchedDocument(
            file_name=f"{safe_title(ref.title)}.md",
            content_type=ContentType.DOCUMENT,
            raw_bytes=text.encode("utf-8"),
            metadata={"provider": self.provider, "external_id": ref.external_id, "title": ref.title, "last_edited": ref.cursor},
        )
