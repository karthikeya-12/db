"""Chat endpoints: conversations (stored per user in Postgres) and a streaming answer endpoint (Server-Sent Events)."""
from __future__ import annotations

import json
import logging
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from ingestion_pipeline.db.models import ChatConversation, ChatMessage, EventConsumerOffset, IngestionEvent
from ingestion_pipeline.db.session import session_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


@lru_cache
def get_agent():
    """The shared chat agent. Overridable in tests (app.dependency_overrides)."""
    from ingestion_pipeline.chat.agent import build_agent

    return build_agent()


class AskBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


def _user_id(request: Request) -> str:
    return request.state.user["user_id"]


def _title_from(message: str) -> str:
    one_line = " ".join(message.split())
    return one_line if len(one_line) <= 60 else one_line[:59].rstrip() + "…"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _conversation_dict(c: ChatConversation) -> dict:
    return {"id": c.id, "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at}


def _owned(session, conversation_id: str, user_id: str) -> ChatConversation:
    conversation = session.get(ChatConversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.get("/status")
def indexing_status() -> dict:
    """How much of what has been ingested is searchable by chat yet (the Cognee indexing queue is slower than filing)."""
    from ingestion_pipeline.consumers.cognee_consumer import CONSUMER_NAME, SKIPPED_CONTENT_TYPES
    from ingestion_pipeline.core import events

    with session_scope() as session:
        offset = session.scalar(
            select(EventConsumerOffset.last_event_id).where(
                EventConsumerOffset.consumer_name == CONSUMER_NAME, EventConsumerOffset.topic == events.TOPIC_ITEM_INGESTED
            )
        ) or 0
        rows = session.execute(
            select(IngestionEvent.event_id, IngestionEvent.payload).where(IngestionEvent.topic == events.TOPIC_ITEM_INGESTED)
        ).all()
    per_source: dict[str, dict] = {}
    for event_id, payload in rows:
        if payload.get("content_type") in SKIPPED_CONTENT_TYPES or not payload.get("normalized_ref"):
            continue
        entry = per_source.setdefault(payload.get("source_id", ""), {"source_id": payload.get("source_id", ""), "total": 0, "pending": 0})
        entry["total"] += 1
        if event_id > offset:
            entry["pending"] += 1
    sources = sorted(per_source.values(), key=lambda e: -e["pending"])
    total = sum(e["total"] for e in sources)
    pending = sum(e["pending"] for e in sources)
    return {"total": total, "pending": pending, "indexed": total - pending, "sources": sources}


@router.get("/conversations")
def list_conversations(request: Request) -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(
            select(ChatConversation).where(ChatConversation.user_id == _user_id(request)).order_by(ChatConversation.updated_at.desc()).limit(100)
        ).all()
        return [_conversation_dict(c) for c in rows]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, request: Request) -> dict:
    with session_scope() as session:
        conversation = _owned(session, conversation_id, _user_id(request))
        messages = session.scalars(select(ChatMessage).where(ChatMessage.conversation_id == conversation.id).order_by(ChatMessage.id)).all()
        return {
            **_conversation_dict(conversation),
            "messages": [{"id": m.id, "role": m.role, "content": m.content, "sources": m.sources or [], "created_at": m.created_at} for m in messages],
        }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, request: Request) -> dict:
    with session_scope() as session:
        conversation = _owned(session, conversation_id, _user_id(request))
        session.execute(delete(ChatConversation).where(ChatConversation.id == conversation.id))
    return {"deleted": conversation_id}


@router.post("/stream")
def ask(body: AskBody, request: Request) -> StreamingResponse:
    """Streams: `conversation` (id, title) -> `sources` -> `token`* -> `done` (message id, final sources), or `error`."""
    user_id = _user_id(request)
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message is empty")

    with session_scope() as session:
        if body.conversation_id:
            conversation = _owned(session, body.conversation_id, user_id)
        else:
            conversation = ChatConversation(user_id=user_id, title=_title_from(message))
            session.add(conversation)
            session.flush()
        conversation_id, title = conversation.id, conversation.title
        history = [
            {"role": m.role, "content": m.content}
            for m in session.scalars(select(ChatMessage).where(ChatMessage.conversation_id == conversation_id).order_by(ChatMessage.id)).all()
        ]
        session.add(ChatMessage(conversation_id=conversation_id, role="user", content=message))

    agent = get_agent()

    def events():
        yield _sse("conversation", {"id": conversation_id, "title": title})
        try:
            for event in agent.stream(message, history):
                kind = event["type"]
                if kind == "done":
                    with session_scope() as session:
                        saved = ChatMessage(conversation_id=conversation_id, role="assistant", content=event["answer"], sources=event["sources"])
                        session.add(saved)
                        session.flush()
                        session.execute(
                            ChatConversation.__table__.update().where(ChatConversation.id == conversation_id).values(updated_at=func.now())
                        )
                        message_id = saved.id
                    yield _sse("done", {"message_id": message_id, "sources": event["sources"]})
                else:
                    yield _sse(kind, {k: v for k, v in event.items() if k != "type"})
        except Exception as exc:
            logger.exception("chat answer failed")
            yield _sse("error", {"message": "The assistant could not answer right now. Please try again.", "detail": str(exc)[:200]})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
