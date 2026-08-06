"""
Embedding with a cache-before-embed check -- ported from the reference
pipeline's chunk_loader.py pattern (check Postgres before calling the
embedding API; only pay for chunks not already cached) onto SQLAlchemy.

Only ever called for chunks produced from DOCUMENT/TRANSCRIPT/MESSAGE
content. TABULAR content never reaches this module.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ingestion_pipeline.core.types import Chunk
from ingestion_pipeline.db.models import ChunkEmbedding


class EmbeddingClient(ABC):
    """Wraps whatever embedding provider is configured. Swappable without touching Embedder."""

    model_name: str

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class OpenAIEmbeddingClient(EmbeddingClient):
    """Lazy-imports the openai package so it's only required if this client is actually used."""

    def __init__(self, api_key: str, model_name: str) -> None:
        self.model_name = model_name
        self._api_key = api_key

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key)
        response = client.embeddings.create(model=self.model_name, input=texts)
        return [item.embedding for item in response.data]


class Embedder:
    def __init__(self, client: EmbeddingClient, batch_size: int = 100) -> None:
        self.client = client
        self._batch_size = batch_size

    def embed_missing(self, session: Session, chunks: list[Chunk]) -> dict[str, list[float]]:
        """Return {chunk_id: vector} for every chunk, calling the embedding API only for
        chunk_ids not already cached under the current model, and writing new results back."""
        if not chunks:
            return {}

        chunk_ids = [c.chunk_id for c in chunks]
        cached_rows = session.execute(
            select(ChunkEmbedding.chunk_id, ChunkEmbedding.embedding).where(
                ChunkEmbedding.chunk_id.in_(chunk_ids),
                ChunkEmbedding.model == self.client.model_name,
            )
        ).all()
        cached: dict[str, list[float]] = {row.chunk_id: list(row.embedding) for row in cached_rows}

        missing = [c for c in chunks if c.chunk_id not in cached]
        for batch_start in range(0, len(missing), self._batch_size):
            batch = missing[batch_start: batch_start + self._batch_size]
            vectors = self.client.embed_batch([c.text for c in batch])
            for chunk, vector in zip(batch, vectors):
                stmt = (
                    pg_insert(ChunkEmbedding)
                    .values(
                        chunk_id=chunk.chunk_id,
                        item_id=chunk.item_id,
                        chunk_index=chunk.chunk_index,
                        model=self.client.model_name,
                        body_text=chunk.text,
                        embedding=vector,
                    )
                    .on_conflict_do_nothing(index_elements=["chunk_id"])
                )
                session.execute(stmt)
                cached[chunk.chunk_id] = vector

        return cached


def get_embedder() -> Embedder:
    from ingestion_pipeline.config import get_settings

    settings = get_settings()
    client = OpenAIEmbeddingClient(api_key=settings.embedding_api_key, model_name=settings.embedding_model)
    return Embedder(client, batch_size=settings.embedding_batch_size)
