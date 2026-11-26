"""The chat agent with a scripted model and retriever (no network, no Cognee)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from ingestion_pipeline.chat.agent import NO_SOURCES_ANSWER, ChatAgent, sources_payload
from ingestion_pipeline.chat.retrieval import Passage, Retrieved, _unwrap, passage_from_payload


class FakeLLM:
    def __init__(self, answer="Ada worked with Babbage [1].", condensed="Ada Lovelace collaborators", route="ARCHIVE"):
        self.answer, self.condensed, self.route, self.calls = answer, condensed, route, []

    def invoke(self, messages):
        if messages[0].content.startswith("Decide whether"):  # the routing call
            self.calls.append(("route", messages))
            return SimpleNamespace(content=self.route)
        self.calls.append(("invoke", messages))
        return SimpleNamespace(content=self.condensed)

    def stream(self, messages):
        self.calls.append(("stream", messages))
        words = self.answer.split(" ")
        for i, w in enumerate(words):
            yield SimpleNamespace(content=w + (" " if i < len(words) - 1 else ""))


class FakeRetriever:
    def __init__(self, passages):
        self.passages, self.queries = passages, []

    def retrieve(self, query, top_k):
        self.queries.append((query, top_k))
        return Retrieved(passages=self.passages, graph_context="Ada -> worked_with -> Babbage")


PASSAGES = [Passage("Ada Lovelace worked with Charles Babbage.", title="ada.md", item_id="local:ada.md", source_id="local_folder")]


def test_streams_sources_then_tokens_then_done_with_citations():
    agent = ChatAgent(FakeRetriever(PASSAGES), FakeLLM(), top_k=5)
    events = list(agent.stream("Who did Ada work with?"))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "sources" and kinds[-1] == "done" and "token" in kinds
    done = events[-1]
    assert done["answer"] == "Ada worked with Babbage [1]."
    assert done["sources"][0]["title"] == "ada.md" and done["sources"][0]["cited"] is True


def test_first_question_is_not_rewritten_but_follow_up_is():
    retriever, llm = FakeRetriever(PASSAGES), FakeLLM()
    agent = ChatAgent(retriever, llm)
    agent.ask("Who did Ada work with?")
    assert retriever.queries[-1][0] == "Who did Ada work with?" and not [c for c in llm.calls if c[0] == "invoke"]

    history = [{"role": "user", "content": "Who did Ada work with?"}, {"role": "assistant", "content": "Babbage [1]."}]
    agent.ask("And what did they build?", history)
    assert retriever.queries[-1][0] == "Ada Lovelace collaborators"


def test_prompt_contains_numbered_sources_graph_and_history():
    llm = FakeLLM()
    ChatAgent(FakeRetriever(PASSAGES), llm).ask("q", [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}])
    stream_call = [c for c in llm.calls if c[0] == "stream"][0][1]
    assert "[1] ada.md" in stream_call[0].content and "worked_with" in stream_call[0].content
    assert [m.content for m in stream_call[1:]] == ["earlier", "reply", "q"]


def test_no_passages_answers_without_calling_the_model():
    llm = FakeLLM()
    done = ChatAgent(FakeRetriever([]), llm).ask("anything")
    assert done["answer"] == NO_SOURCES_ANSWER and done["sources"] == []
    assert not [c for c in llm.calls if c[0] == "stream"]


def test_history_is_limited_to_recent_turns():
    llm = FakeLLM()
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(10)]
    ChatAgent(FakeRetriever(PASSAGES), llm, history_turns=4).ask("q", history)
    stream_call = [c for c in llm.calls if c[0] == "stream"][0][1]
    assert [m.content for m in stream_call[1:-1]] == ["m6", "m7", "m8", "m9"]


def test_sources_payload_marks_only_cited_and_truncates():
    long = Passage("x" * 1000, title="t")
    payload = sources_payload([PASSAGES[0], long], "see [1]")
    assert [s["cited"] for s in payload] == [True, False] and len(payload[1]["snippet"]) < 500


def test_cognee_payload_normalisation():
    chunk = {"text": " hello ", "document_name": "fallback.md", "document_id": "d1", "chunk_index": 2,
             "external_metadata": json.dumps({"item_id": "local:a.md", "source_id": "local_folder", "file_name": "a.md"})}
    wrapped = [SimpleNamespace(search_result=[chunk, {"text": ""}]), {"search_result": chunk}]
    payloads = _unwrap(wrapped)
    assert len(payloads) == 2
    passage = passage_from_payload(payloads[0])
    assert (passage.text, passage.title, passage.item_id, passage.chunk_index) == ("hello", "a.md", "local:a.md", 2)
    assert passage_from_payload({"text": "  "}) is None
    assert passage_from_payload({"text": "t", "document_name": "doc.md"}).title == "doc.md"


def test_evaluation_scoring():
    import json
    from pathlib import Path

    from ingestion_pipeline.chat.evaluation import score_case, summarize

    cases = json.loads((Path(__file__).resolve().parents[1] / "evals" / "cases.json").read_text())
    assert len({c["id"] for c in cases}) == len(cases)
    case = {"id": "x", "must_include": ["25"], "cite": "a.md"}
    good = score_case(case, "Employees get 25 days [1].", [{"title": "a.md", "cited": True}])
    assert good.passed
    bad = score_case(case, "Around 20 days.", [{"title": "b.md", "cited": True}])
    assert not bad.passed and len(bad.problems) == 2
    assert not score_case(case, "25 days", []).passed  # right fact, no citation
    refusal = {"id": "r", "answerable": False}
    assert score_case(refusal, "I couldn't find that in the archive.", []).passed
    assert not score_case(refusal, "The CEO is Jane.", []).passed
    assert summarize([good, bad]) == {"total": 2, "passed": 1, "failed": ["x"]}


def test_unwrap_handles_nested_and_model_shapes():
    class Model:
        def model_dump(self):
            return {"text": "from model"}

    wrapped = [{"dataset_name": "d", "search_result": [[{"text": "nested"}], Model()]}, SimpleNamespace(payload={"text": "payload attr"})]
    texts = [p["text"] if isinstance(p, dict) else p.payload["text"] for p in _unwrap(wrapped)]
    assert texts == ["nested", "from model", "payload attr"]


def test_general_questions_skip_the_archive():
    retriever, llm = FakeRetriever(PASSAGES), FakeLLM(answer="Hello! How can I help?", route="GENERAL")
    events = list(ChatAgent(retriever, llm).stream("hi there"))
    assert retriever.queries == []  # no knowledge-base search
    assert events[0] == {"type": "route", "route": "general"}
    done = events[-1]
    assert done["answer"] == "Hello! How can I help?" and done["sources"] == []


def test_routing_failure_falls_back_to_the_archive():
    class Broken(FakeLLM):
        def invoke(self, messages):
            if messages[0].content.startswith("Decide whether"):
                raise RuntimeError("down")
            return super().invoke(messages)

    retriever = FakeRetriever(PASSAGES)
    done = ChatAgent(retriever, Broken()).ask("anything")
    assert retriever.queries and done["sources"][0]["cited"] is True
