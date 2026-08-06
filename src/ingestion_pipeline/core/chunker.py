"""
Chunking strategies, selected by content_type. Only DOCUMENT/TRANSCRIPT/MESSAGE
content is ever chunked -- TABULAR content must never reach this module; the
orchestrator branches before calling get_chunker() so that's enforced structurally,
not just by convention.

WordWindowChunker's split-point search is adapted from the reference
pipeline's char-based chunk_size_enforcer.py (paragraph break > sentence end >
hard cut), re-thresholded from a character budget to a word budget.
"""
from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod

from ingestion_pipeline.core.types import Chunk, ContentType

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")


def _chunk_id(item_id: str, chunk_index: int, text: str) -> str:
    """Stable, content-derived chunk id -- re-chunking identical content reproduces the same id."""
    raw = f"{item_id}||{chunk_index}||{text}"
    return "CHK_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class Chunker(ABC):
    @abstractmethod
    def chunk(self, item_id: str, text: str) -> list[Chunk]:
        raise NotImplementedError


class WordWindowChunker(Chunker):
    """Fixed word-window strategy for DOCUMENT content: ~min_words-max_words per chunk,
    overlap_words carried into the next chunk. Split preferentially at a paragraph
    break, then a sentence end, and only as a last resort at a hard word-count cut."""

    def __init__(self, min_words: int = 400, max_words: int = 500, overlap_words: int = 50) -> None:
        self.min_words = min_words
        self.max_words = max_words
        self.overlap_words = overlap_words

    def chunk(self, item_id: str, text: str) -> list[Chunk]:
        words = text.split()
        if not words:
            return []
        if len(words) <= self.max_words:
            return [Chunk(chunk_id=_chunk_id(item_id, 0, text), item_id=item_id, chunk_index=0, text=text)]

        chunks: list[Chunk] = []
        start = 0
        index = 0
        while start < len(words):
            end = min(start + self.max_words, len(words))
            if end < len(words):
                end = self._find_split_point(words, start, end)
            piece_text = " ".join(words[start:end])
            chunks.append(
                Chunk(chunk_id=_chunk_id(item_id, index, piece_text), item_id=item_id, chunk_index=index, text=piece_text)
            )
            index += 1
            if end >= len(words):
                break
            start = max(start + 1, end - self.overlap_words)
        return chunks

    def _find_split_point(self, words: list[str], start: int, target_end: int) -> int:
        """Search backwards from target_end for a paragraph break, then a sentence end,
        preferring not to cut mid-sentence. Falls back to a hard cut at target_end."""
        window_text = " ".join(words[start:target_end])

        para_matches = list(_PARAGRAPH_RE.finditer(window_text))
        if para_matches:
            cut_char = para_matches[-1].end()
            candidate = start + len(window_text[:cut_char].split())
            if candidate > start:
                return candidate

        sent_matches = list(_SENTENCE_END_RE.finditer(window_text))
        if sent_matches:
            cut_char = sent_matches[-1].end()
            candidate = start + len(window_text[:cut_char].split())
            if candidate > start:
                return candidate

        return target_end  # hard cut -- no good boundary found in this window


class SentenceBoundaryChunker(Chunker):
    """Sentence-boundary strategy for TRANSCRIPT content: groups consecutive sentences
    up to a target word size, never splitting a sentence in half."""

    def __init__(self, target_words: int = 150) -> None:
        self.target_words = target_words

    def chunk(self, item_id: str, text: str) -> list[Chunk]:
        sentences = [s.strip() for s in _SENTENCE_END_RE.split(text) if s.strip()]
        if not sentences:
            return []

        chunks: list[Chunk] = []
        buffer: list[str] = []
        word_count = 0
        index = 0
        for sentence in sentences:
            sentence_words = len(sentence.split())
            if buffer and word_count + sentence_words > self.target_words:
                piece_text = " ".join(buffer)
                chunks.append(Chunk(chunk_id=_chunk_id(item_id, index, piece_text), item_id=item_id, chunk_index=index, text=piece_text))
                index += 1
                buffer, word_count = [], 0
            buffer.append(sentence)
            word_count += sentence_words
        if buffer:
            piece_text = " ".join(buffer)
            chunks.append(Chunk(chunk_id=_chunk_id(item_id, index, piece_text), item_id=item_id, chunk_index=index, text=piece_text))
        return chunks


class ThreadLevelChunker(Chunker):
    """Thread-level strategy for MESSAGE content: keeps a whole conversation thread as
    one chunk unless it exceeds max_words, in which case it splits at message
    boundaries (messages are joined by MESSAGE_DELIMITER by the caller)."""

    MESSAGE_DELIMITER = "\n---\n"

    def __init__(self, max_words: int = 500) -> None:
        self.max_words = max_words

    def chunk(self, item_id: str, text: str) -> list[Chunk]:
        if len(text.split()) <= self.max_words:
            return [Chunk(chunk_id=_chunk_id(item_id, 0, text), item_id=item_id, chunk_index=0, text=text)]

        messages = text.split(self.MESSAGE_DELIMITER)
        chunks: list[Chunk] = []
        buffer: list[str] = []
        word_count = 0
        index = 0
        for message in messages:
            message_words = len(message.split())
            if buffer and word_count + message_words > self.max_words:
                piece_text = self.MESSAGE_DELIMITER.join(buffer)
                chunks.append(Chunk(chunk_id=_chunk_id(item_id, index, piece_text), item_id=item_id, chunk_index=index, text=piece_text))
                index += 1
                buffer, word_count = [], 0
            buffer.append(message)
            word_count += message_words
        if buffer:
            piece_text = self.MESSAGE_DELIMITER.join(buffer)
            chunks.append(Chunk(chunk_id=_chunk_id(item_id, index, piece_text), item_id=item_id, chunk_index=index, text=piece_text))
        return chunks


def get_chunker(content_type: ContentType) -> Chunker:
    from ingestion_pipeline.config import get_settings

    settings = get_settings()
    if content_type == ContentType.DOCUMENT:
        return WordWindowChunker(settings.chunk_min_words, settings.chunk_max_words, settings.chunk_overlap_words)
    if content_type == ContentType.TRANSCRIPT:
        return SentenceBoundaryChunker()
    if content_type == ContentType.MESSAGE:
        return ThreadLevelChunker(settings.chunk_max_words)
    raise ValueError(
        f"content_type={content_type!r} is never chunked (TABULAR/STRUCTURED_RECORD skip chunking by design)"
    )
