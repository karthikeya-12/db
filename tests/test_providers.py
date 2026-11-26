"""Provider fetchers against mocked HTTP APIs (response shapes follow each provider's documented API)."""
from __future__ import annotations

import base64
import json

import httpx

from ingestion_pipeline.connectors.providers import get_fetcher
from ingestion_pipeline.connectors.providers.base import ChangeRef, html_to_text, safe_title
from ingestion_pipeline.core.types import ContentType


def http(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


# ------------------------------------------------------------------------------- helpers


def test_html_to_text_and_titles():
    assert html_to_text("<p>Hello <b>world</b></p><script>x()</script><div>Bye</div>") == "Hello world\n\nBye"
    assert safe_title('a/b:c*"d"?') == "a b c d"
    assert safe_title("   ") == "Untitled"
    assert len(safe_title("x" * 300)) == 80


# --------------------------------------------------------------------------------- Gmail

GMAIL = {
    "m1": {"internalDate": "1000000", "subject": "First", "text": "hello one"},
    "m2": {"internalDate": "2000000", "subject": "Second", "html": "<p>hello <b>two</b></p>"},
    "m3": {"internalDate": "3000000", "subject": "Third", "text": "three", "attachment": "plan.pdf"},
}


def gmail_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == "Bearer TOKEN"
    path = request.url.path
    if path.endswith("/messages"):
        q = request.url.params["q"]
        after = int(q.split(":")[1]) if q.startswith("after:") else 0
        ids = [k for k in ("m3", "m2", "m1") if int(GMAIL[k]["internalDate"]) // 1000 >= after]  # newest first
        return httpx.Response(200, json={"messages": [{"id": i} for i in ids]})
    message_id = path.rsplit("/", 1)[1]
    m = GMAIL[message_id]
    headers = [{"name": "Subject", "value": m["subject"]}, {"name": "From", "value": "a@x.com"}, {"name": "To", "value": "b@x.com"}]
    if request.url.params["format"] == "metadata":
        return httpx.Response(200, json={"id": message_id, "internalDate": m["internalDate"], "payload": {"headers": headers}})
    parts = []
    if "text" in m:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(m["text"])}})
    if "html" in m:
        parts.append({"mimeType": "text/html", "body": {"data": b64(m["html"])}})
    if "attachment" in m:
        parts.append({"mimeType": "application/pdf", "filename": m["attachment"], "body": {"attachmentId": "a1"}})
    return httpx.Response(
        200, json={"id": message_id, "internalDate": m["internalDate"], "snippet": "snip", "payload": {"headers": headers, "mimeType": "multipart/mixed", "parts": parts}}
    )


def test_gmail_lists_oldest_first_respecting_cursor_and_limit():
    f = get_fetcher("google-mail", http(gmail_handler))
    refs = f.list_changes("TOKEN", None, limit=2, initial_days=30)
    assert [r.external_id for r in refs] == ["m1", "m2"]
    refs = f.list_changes("TOKEN", "2000000", limit=10, initial_days=30)
    assert [r.external_id for r in refs] == ["m3"]  # strictly after the cursor
    assert refs[0].cursor == "3000000"


def test_gmail_latest_takes_newest_unseen_messages_only():
    f = get_fetcher("google-mail", http(gmail_handler))
    refs = f.list_latest("TOKEN", limit=2, initial_days=30, skip_ids=set())
    assert [r.external_id for r in refs] == ["m2", "m3"]  # the two newest, returned oldest first
    refs = f.list_latest("TOKEN", limit=2, initial_days=30, skip_ids={"m3"})
    assert [r.external_id for r in refs] == ["m1", "m2"]
    assert f.list_latest("TOKEN", limit=5, initial_days=30, skip_ids={"m1", "m2", "m3"}) == []


def test_gmail_fetch_builds_markdown():
    f = get_fetcher("google-mail", http(gmail_handler))
    plain = f.fetch("TOKEN", ChangeRef("m3", "Third", "3000000"))
    text = plain.raw_bytes.decode()
    assert text.startswith("# Third") and "- From: a@x.com" in text and "three" in text and "plan.pdf" in text
    assert plain.content_type == ContentType.MESSAGE and plain.file_name == "Third.md"
    html = f.fetch("TOKEN", ChangeRef("m2", "Second", "2000000")).raw_bytes.decode()
    assert "hello two" in html and "<b>" not in html


# ------------------------------------------------------------------------------ Calendar


def test_calendar_orders_by_updated_and_skips_cancelled():
    events = [
        {"id": "e2", "summary": "Later", "updated": "2026-02-02T00:00:00Z", "start": {"dateTime": "2026-03-01T10:00:00Z"}, "end": {"dateTime": "2026-03-01T11:00:00Z"}, "attendees": [{"email": "p@x.com"}], "description": "Agenda"},
        {"id": "e1", "summary": "Earlier", "updated": "2026-02-01T00:00:00Z", "start": {"date": "2026-03-02"}, "end": {"date": "2026-03-03"}},
        {"id": "e0", "status": "cancelled", "updated": "2026-02-03T00:00:00Z"},
    ]
    f = get_fetcher("google-calendar", http(lambda r: httpx.Response(200, json={"items": events})))
    refs = f.list_changes("T", None, 10, 30)
    assert [r.external_id for r in refs] == ["e1", "e2"]
    doc = f.fetch("T", refs[1]).raw_bytes.decode()
    assert "# Later" in doc and "p@x.com" in doc and "Agenda" in doc
    assert [r.external_id for r in f.list_changes("T", "2026-02-01T00:00:00Z", 10, 30)] == ["e2"]


# --------------------------------------------------------------------------------- Drive


def test_drive_exports_google_docs_and_skips_unsupported():
    files = [
        {"id": "d1", "name": "Plan", "mimeType": "application/vnd.google-apps.document", "modifiedTime": "2026-01-01T00:00:00Z"},
        {"id": "d2", "name": "notes.txt", "mimeType": "text/plain", "modifiedTime": "2026-01-02T00:00:00Z", "size": "10"},
        {"id": "d3", "name": "movie.mp4", "mimeType": "video/mp4", "modifiedTime": "2026-01-03T00:00:00Z", "size": "10"},
        {"id": "d4", "name": "Form", "mimeType": "application/vnd.google-apps.form", "modifiedTime": "2026-01-04T00:00:00Z"},
        {"id": "d5", "name": "Budget", "mimeType": "application/vnd.google-apps.spreadsheet", "modifiedTime": "2026-01-05T00:00:00Z"},
    ]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"files": files})
        return httpx.Response(200, content=b"BYTES")

    f = get_fetcher("google-drive", http(handler))
    refs = f.list_changes("T", None, 10, 30)
    assert [(r.external_id, r.extra["file_name"]) for r in refs] == [("d1", "Plan.docx"), ("d2", "notes.txt"), ("d5", "Budget.xlsx")]
    doc = f.fetch("T", refs[0])
    assert doc.raw_bytes == b"BYTES" and doc.content_type == ContentType.DOCUMENT
    assert any("/d1/export" in c and "wordprocessingml" in c for c in calls)
    assert f.fetch("T", refs[2]).content_type == ContentType.TABULAR
    f.fetch("T", refs[1])
    assert any("/d2?" in c and "alt=media" in c for c in calls)


# --------------------------------------------------------------------------------- Slack


def test_slack_groups_by_channel_and_day_and_resolves_names():
    # 2026-03-01 10:00 UTC and 11:00 UTC, and 2026-03-02 09:00 UTC
    t1, t2, t3 = 1772359200.0, 1772362800.0, 1772442000.0

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        if method == "conversations.list":
            return httpx.Response(200, json={"ok": True, "channels": [{"id": "C1", "name": "general", "is_member": True}, {"id": "C2", "name": "other", "is_member": False}]})
        if method == "users.list":
            return httpx.Response(200, json={"ok": True, "members": [{"id": "U1", "real_name": "Ada"}]})
        if method == "conversations.history":
            oldest = float(request.url.params["oldest"])
            latest = float(request.url.params["latest"]) if "latest" in request.url.params else 1e12
            msgs = [{"ts": str(t), "user": "U1", "text": f"msg {t}"} for t in (t3, t2, t1) if oldest < t < latest]
            msgs.append({"ts": str(t1 + 1) if oldest < t1 + 1 < latest else "0", "subtype": "channel_join", "user": "U1", "text": "joined"})
            return httpx.Response(200, json={"ok": True, "messages": [m for m in msgs if m["ts"] != "0"], "has_more": False})
        return httpx.Response(500)

    f = get_fetcher("slack", http(handler))
    refs = f.list_changes("T", "1772359000.0", 10, 30)
    assert [r.external_id for r in refs] == ["C1/2026-03-01", "C1/2026-03-02"]
    assert float(refs[0].cursor) == t2  # the day's newest real message (joins are ignored)
    day1 = f.fetch("T", refs[0])
    text = day1.raw_bytes.decode()
    assert "**Ada** (10:00 UTC)" in text and "**Ada** (11:00 UTC)" in text and "joined" not in text
    assert day1.content_type == ContentType.TRANSCRIPT


def test_slack_api_error_is_reported():
    import pytest

    from ingestion_pipeline.connectors.providers.base import ProviderError

    f = get_fetcher("slack", http(lambda r: httpx.Response(200, json={"ok": False, "error": "invalid_auth"})))
    with pytest.raises(ProviderError, match="invalid_auth"):
        f.list_changes("T", None, 10, 30)


# -------------------------------------------------------------------------------- Notion


def test_notion_search_stops_at_cursor_and_renders_blocks():
    pages = [
        {"id": "p2", "last_edited_time": "2026-03-02T00:00:00.000Z", "properties": {"Name": {"type": "title", "title": [{"plain_text": "Roadmap"}]}}},
        {"id": "p1", "last_edited_time": "2026-03-01T00:00:00.000Z", "properties": {"Name": {"type": "title", "title": [{"plain_text": "Old"}]}}},
        {"id": "pz", "last_edited_time": "2026-01-01T00:00:00.000Z", "properties": {}},
    ]
    blocks = [
        {"id": "b1", "type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Goals"}]}, "has_children": False},
        {"id": "b2", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "Ship it"}]}, "has_children": False},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["notion-version"] == "2022-06-28"
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": pages, "has_more": False})
        return httpx.Response(200, json={"results": blocks, "has_more": False})

    f = get_fetcher("notion", http(handler))
    refs = f.list_changes("T", "2026-02-01T00:00:00.000Z", 10, 30)
    assert [r.external_id for r in refs] == ["p1", "p2"]  # oldest first; pz is older than the cursor
    doc = f.fetch("T", refs[1]).raw_bytes.decode()
    assert doc.startswith("# Roadmap") and "# Goals" in doc and "- Ship it" in doc


def test_unknown_provider():
    import pytest

    with pytest.raises(KeyError):
        get_fetcher("myspace")


# ------------------------------------------------------------------------ more providers


def test_outlook_lists_after_cursor_and_renders_html():
    def handler(request):
        assert request.headers["authorization"] == "Bearer TOKEN"
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"value": [
                {"id": "a", "subject": "Old", "receivedDateTime": "2026-01-01T00:00:00Z"},
                {"id": "b", "subject": "New", "receivedDateTime": "2026-01-02T00:00:00Z"},
            ]})
        return httpx.Response(200, json={
            "subject": "New", "receivedDateTime": "2026-01-02T00:00:00Z", "hasAttachments": True,
            "from": {"emailAddress": {"address": "a@x.com"}}, "toRecipients": [{"emailAddress": {"address": "b@x.com"}}],
            "body": {"contentType": "html", "content": "<p>Hi <b>there</b></p>"},
        })

    f = get_fetcher("outlook", http(handler))
    refs = f.list_changes("TOKEN", "2026-01-01T00:00:00Z", 10, 30)
    assert [r.external_id for r in refs] == ["b"]
    doc = f.fetch("TOKEN", refs[0])
    assert doc.content_type == ContentType.MESSAGE and b"Hi there" in doc.raw_bytes and b"From: a@x.com" in doc.raw_bytes


def test_onedrive_skips_folders_and_downloads_without_bearer():
    def handler(request):
        if request.url.host == "download.example":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=b"pdf-bytes")
        if request.url.path.endswith("/delta"):
            return httpx.Response(200, json={"value": [
                {"id": "f1", "name": "folder", "lastModifiedDateTime": "2999-01-01T00:00:00Z"},
                {"id": "d1", "name": "plan.pdf", "file": {}, "size": 10, "lastModifiedDateTime": "2999-01-02T00:00:00Z"},
                {"id": "x1", "name": "tool.exe", "file": {}, "size": 10, "lastModifiedDateTime": "2999-01-03T00:00:00Z"},
            ]})
        return httpx.Response(200, json={"@microsoft.graph.downloadUrl": "https://download.example/d1"})

    for provider in ("one-drive", "one-drive-personal"):
        f = get_fetcher(provider, http(handler))
        refs = f.list_changes("TOKEN", None, 10, 30)
        assert [r.external_id for r in refs] == ["d1"]
        assert f.fetch("TOKEN", refs[0]).raw_bytes == b"pdf-bytes"


def test_dropbox_pages_and_downloads():
    def handler(request):
        path = request.url.path
        if path.endswith("/list_folder"):
            return httpx.Response(200, json={"entries": [{".tag": "folder", "name": "d", "server_modified": "2999-01-01T00:00:00Z"}],
                                             "has_more": True, "cursor": "c1"})
        if path.endswith("/list_folder/continue"):
            return httpx.Response(200, json={"entries": [{".tag": "file", "id": "id:1", "name": "n.md", "path_display": "/n.md",
                                                          "size": 5, "server_modified": "2999-01-02T00:00:00Z"}], "has_more": False})
        assert json.loads(request.headers["dropbox-api-arg"]) == {"path": "id:1"}
        return httpx.Response(200, content=b"# note")

    f = get_fetcher("dropbox", http(handler))
    refs = f.list_changes("TOKEN", None, 10, 30)
    assert [r.title for r in refs] == ["/n.md"]
    assert f.fetch("TOKEN", refs[0]).raw_bytes == b"# note"


def test_github_lists_issues_and_includes_comments():
    def handler(request):
        if request.url.path == "/issues":
            return httpx.Response(200, json=[
                {"id": 1, "number": 7, "title": "Old", "updated_at": "2026-01-01T00:00:00Z", "url": "https://api.github.com/repos/o/r/issues/7",
                 "repository": {"full_name": "o/r"}},
                {"id": 2, "number": 8, "title": "Bug", "updated_at": "2026-01-02T00:00:00Z", "url": "https://api.github.com/repos/o/r/issues/8",
                 "comments_url": "https://api.github.com/repos/o/r/issues/8/comments", "repository": {"full_name": "o/r"}},
            ])
        if request.url.path.endswith("/comments"):
            return httpx.Response(200, json=[{"user": {"login": "bob"}, "created_at": "2026-01-03", "body": "me too"}])
        return httpx.Response(200, json={"state": "open", "user": {"login": "amy"}, "body": "it breaks", "comments": 1, "labels": [], "html_url": "u"})

    f = get_fetcher("github", http(handler))
    refs = f.list_changes("TOKEN", "2026-01-01T00:00:00Z", 10, 30)
    assert [r.title for r in refs] == ["o/r#8 Bug"]
    text = f.fetch("TOKEN", refs[0]).raw_bytes.decode()
    assert "it breaks" in text and "bob" in text and "me too" in text


def test_jira_normalizes_timestamps_and_reads_adf():
    def handler(request):
        path = request.url.path
        if path.endswith("accessible-resources"):
            return httpx.Response(200, json=[{"id": "cloud1", "url": "https://acme.atlassian.net"}])
        if path.endswith("/search/jql"):
            return httpx.Response(200, json={"issues": [
                {"key": "AB-1", "fields": {"summary": "Old", "updated": "2026-01-01T00:00:00.000+0000"}},
                {"key": "AB-2", "fields": {"summary": "New", "updated": "2026-01-02T10:00:00.000+0100"}},
            ]})
        return httpx.Response(200, json={"fields": {
            "status": {"name": "Open"}, "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Broken login"}]}]},
            "comment": {"comments": [{"author": {"displayName": "Zed"}, "created": "x", "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "on it"}]}]}}]},
        }})

    f = get_fetcher("jira", http(handler))
    refs = f.list_changes("TOKEN", "2026-01-01T00:00:00.000Z", 10, 30)
    assert [r.title for r in refs] == ["AB-2 New"] and refs[0].cursor == "2026-01-02T09:00:00.000Z"
    text = f.fetch("TOKEN", refs[0]).raw_bytes.decode()
    assert "Broken login" in text and "on it" in text and "acme.atlassian.net/browse/AB-2" in text


def test_confluence_converts_storage_html():
    def handler(request):
        path = request.url.path
        if path.endswith("accessible-resources"):
            return httpx.Response(200, json=[{"id": "cloud1", "url": "https://acme.atlassian.net"}])
        if path.endswith("/content/search"):
            return httpx.Response(200, json={"results": [{"id": "9", "title": "Runbook", "version": {"when": "2026-02-01T00:00:00.000Z"}}]})
        return httpx.Response(200, json={"body": {"storage": {"value": "<h1>Steps</h1><p>Restart it</p>"}}})

    f = get_fetcher("confluence", http(handler))
    refs = f.list_changes("TOKEN", None, 10, 30)
    assert refs[0].external_id == "cloud1:9"
    assert "Restart it" in f.fetch("TOKEN", refs[0]).raw_bytes.decode()
