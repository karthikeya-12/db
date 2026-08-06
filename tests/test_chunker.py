from ingestion_pipeline.core.chunker import SentenceBoundaryChunker, WordWindowChunker


def test_word_window_chunker_short_text_single_chunk():
    chunker = WordWindowChunker(min_words=400, max_words=500, overlap_words=50)
    text = ("word " * 100).strip()
    chunks = chunker.chunk("item-1", text)
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0


def test_word_window_chunker_splits_long_text():
    chunker = WordWindowChunker(min_words=400, max_words=500, overlap_words=50)
    text = " ".join(f"word{i}" for i in range(1200))
    chunks = chunker.chunk("item-2", text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.text.split()) <= 505  # small tolerance for boundary search


def test_word_window_chunker_stable_chunk_ids_for_same_content():
    chunker = WordWindowChunker(min_words=400, max_words=500, overlap_words=50)
    text = " ".join(f"word{i}" for i in range(1200))
    first = [c.chunk_id for c in chunker.chunk("item-3", text)]
    second = [c.chunk_id for c in chunker.chunk("item-3", text)]
    assert first == second


def test_sentence_boundary_chunker_groups_sentences():
    chunker = SentenceBoundaryChunker(target_words=10)
    text = "One two three. Four five six. Seven eight nine ten eleven twelve."
    chunks = chunker.chunk("item-4", text)
    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.text.strip() != ""
