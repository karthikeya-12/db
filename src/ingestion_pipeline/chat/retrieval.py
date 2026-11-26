"""Retrieval for chat: passages (with their source document) and related graph facts, both from Cognee."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class Passage:
    text: str
    title: str = "Untitled"
    item_id: str = ""
    source_id: str = ""
    document_id: str = ""
    chunk_index: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Retrieved:
    passages: list[Passage] = field(default_factory=list)
    graph_context: str = ""


class Retriever(Protocol):
    def warm_up(self) -> None:
        """Pay Cognee's slow first-call costs (imports, opening the vector store and graph) ahead of the first question."""
        try:
            run_on_cognee_loop_(self._retrieve("warm up", 1), 300.0)
        except Exception as exc:
            logger.info("chat warm-up skipped: %s", exc)

    def retrieve(self, query: str, top_k: int) -> Retrieved: ...


def _unwrap(results: Any) -> list[Any]:
    """Cognee wraps results differently across search paths (SearchResult objects, dicts, nested lists,
    models with a `payload`). Walk whatever comes back and collect every chunk-like payload (anything with `text`)."""
    found: list[Any] = []

    def walk(node: Any, depth: int = 0) -> None:
        if node is None or depth > 6:
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                walk(child, depth + 1)
            return
        if isinstance(node, str):
            return
        if hasattr(node, "model_dump") and not isinstance(node, dict):
            try:
                node = node.model_dump()
            except Exception:
                pass
        if not isinstance(node, dict):
            for attr in ("search_result", "payload"):
                if hasattr(node, attr):
                    walk(getattr(node, attr), depth + 1)
                    return
            if hasattr(node, "text"):
                found.append(node)
            return
        if node.get("text"):
            found.append(node)
            return
        for key in ("search_result", "payload", "results", "chunks"):
            if key in node:
                walk(node[key], depth + 1)
                return

    walk(results)
    return found


def _field(obj: Any, name: str, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def passage_from_payload(payload: Any) -> Passage | None:
    text = _field(payload, "text")
    if not text or not str(text).strip():
        return None
    meta = _field(payload, "external_metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    title = meta.get("file_name") or meta.get("title") or _field(payload, "document_name") or "Untitled"
    return Passage(
        text=str(text).strip(),
        title=str(title),
        item_id=str(meta.get("item_id", "")),
        source_id=str(meta.get("source_id", "")),
        document_id=str(_field(payload, "document_id", "") or ""),
        chunk_index=_field(payload, "chunk_index"),
    )


def _context_text(results: Any) -> str:
    parts = [p if isinstance(p, str) else json.dumps(p, default=str) for p in _unwrap(results)]
    return "\n".join(parts).strip()


def run_on_cognee_loop_(coroutine, timeout):
    from ingestion_pipeline.core.cognee_indexer import run_on_cognee_loop

    return run_on_cognee_loop(coroutine, timeout)


class CogneeRetriever:
    """Searches every dataset: chunk search for citable passages, graph search for connected facts."""

    def __init__(self, cognee_module: Any = None, use_graph: bool = True, timeout: float = 180.0, graph_timeout: float = 25.0) -> None:
        self._graph_timeout = graph_timeout
        self._cognee = cognee_module
        self._use_graph = use_graph
        self._timeout = timeout

    def _module(self):
        if self._cognee is None:
            from ingestion_pipeline.core.cognee_setup import get_cognee

            self._cognee = get_cognee()
        return self._cognee

    def warm_up(self) -> None:
        """Pay Cognee's slow first-call costs (imports, opening the vector store and graph) ahead of the first question."""
        try:
            run_on_cognee_loop_(self._retrieve("warm up", 1), 300.0)
        except Exception as exc:
            logger.info("chat warm-up skipped: %s", exc)

    def retrieve(self, query: str, top_k: int) -> Retrieved:
        from ingestion_pipeline.core.cognee_indexer import run_on_cognee_loop

        return run_on_cognee_loop(self._retrieve(query, top_k), self._timeout)

    async def _retrieve(self, query: str, top_k: int) -> Retrieved:
        import asyncio

        from cognee import SearchType

        cognee = self._module()

        async def chunks():
            try:
                return await cognee.search(query_text=query, query_type=SearchType.CHUNKS, top_k=top_k)
            except Exception as exc:  # an empty / never-indexed store raises rather than returning nothing
                logger.info("chunk search found nothing (%s)", exc)
                return []

        async def graph():
            if not self._use_graph:
                return ""
            try:  # background context only: never let a slow graph walk hold up the answer
                raw_graph = await asyncio.wait_for(
                    cognee.search(query_text=query, query_type=SearchType.GRAPH_COMPLETION, only_context=True, top_k=top_k),
                    timeout=self._graph_timeout,
                )
                return _context_text(raw_graph)[:4000]
            except Exception as exc:
                logger.warning("graph context unavailable: %s", exc)
                return ""

        raw, graph_context = await asyncio.gather(chunks(), graph())  # the two searches are independent
        passages: list[Passage] = []
        seen: set[str] = set()
        payloads = _unwrap(raw)
        if raw and not payloads:
            logger.warning("chunk search returned results this app could not read; shape: %r", str(raw)[:500])
        for payload in payloads:
            passage = passage_from_payload(payload)
            if passage and passage.text not in seen:
                seen.add(passage.text)
                passages.append(passage)
        return Retrieved(passages=passages[:top_k], graph_context=graph_context if passages else "")
