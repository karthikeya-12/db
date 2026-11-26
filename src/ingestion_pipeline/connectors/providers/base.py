"""Shared pieces for provider fetchers: change refs, fetched documents, HTTP with retries, text helpers."""
from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx

from ingestion_pipeline.core.types import ContentType


class ProviderError(Exception):
    """A provider API call failed in a way retrying later may fix (or the credentials need renewing)."""


@dataclass
class ChangeRef:
    """One new-or-changed thing found at a provider. `cursor` is the checkpoint to store once it is handled;
    fetchers return refs in ascending cursor order so the checkpoint only ever moves forward."""

    external_id: str
    title: str
    cursor: str
    extra: dict = field(default_factory=dict)


@dataclass
class FetchedDocument:
    file_name: str
    content_type: ContentType
    raw_bytes: bytes
    metadata: dict = field(default_factory=dict)


class ProviderFetcher(ABC):
    """Reads one provider's API using an OAuth access token (obtained from Nango by the adapter)."""

    provider: str  # Nango provider key, e.g. "google-mail"

    def __init__(self, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=30.0)

    @abstractmethod
    def list_changes(self, token: str, cursor: str | None, limit: int, initial_days: int) -> list[ChangeRef]:
        """New or changed items since `cursor` (None on the first run: only the last `initial_days`), oldest first, at most `limit`."""

    def list_latest(self, token: str, limit: int, initial_days: int, skip_ids: set[str]) -> list[ChangeRef] | None:
        """The newest `limit` items from the last `initial_days` that are not in `skip_ids`, oldest first.
        Used when an account is capped to a number of items; providers that cannot do this return None."""
        return None

    @abstractmethod
    def fetch(self, token: str, ref: ChangeRef) -> FetchedDocument:
        """The full content of one item."""

    # --------------------------------------------------------------- http helpers

    def request(self, method: str, url: str, token: str, *, params=None, json=None, headers=None) -> httpx.Response:
        merged = {"Authorization": f"Bearer {token}", **(headers or {})}
        last: Exception | None = None
        for attempt in range(4):
            try:
                response = self._http.request(method, url, params=params, json=json, headers=merged)
            except httpx.HTTPError as exc:
                last = exc
                time.sleep(min(2**attempt, 8) * 0.5)
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                retry_after = response.headers.get("retry-after", "")
                time.sleep(min(float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2**attempt, 30))
                last = ProviderError(f"{url} answered {response.status_code}")
                continue
            if response.status_code in (401, 403):
                raise ProviderError(f"{self.provider}: access refused ({response.status_code}); reconnect the account")
            if response.status_code >= 400:
                raise ProviderError(f"{self.provider}: {url} answered {response.status_code}: {response.text[:200]}")
            return response
        raise ProviderError(f"{self.provider}: request failed after retries: {last}")

    def get_json(self, url: str, token: str, **kwargs) -> dict:
        return self.request("GET", url, token, **kwargs).json()


# ---------------------------------------------------------------------------- text helpers


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
    _SKIP = {"script", "style", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_title(title: str | None, fallback: str = "Untitled", limit: int = 80) -> str:
    cleaned = _UNSAFE.sub(" ", (title or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned[:limit].rstrip() or fallback)
