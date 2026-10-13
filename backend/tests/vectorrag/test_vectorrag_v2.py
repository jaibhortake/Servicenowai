import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.vectorrag_v2.indexer import VectorRAGIndexer  # noqa: E402
from app.services.vectorrag_v2.service import vectorrag_v2_service  # noqa: E402


def test_vectorrag_indexer_splits_paragraphs_and_ignores_empty_blocks():
    indexer = VectorRAGIndexer()
    raw = "First solution.\n\n\nSecond solution.\n\n   \nThird solution."
    parts = indexer._split_paragraphs(raw)
    assert parts == ["First solution.", "Second solution.", "Third solution."]


def test_vectorrag_indexer_uses_stable_doc_id_and_dedupes_duplicate_paragraphs():
    indexer = VectorRAGIndexer()
    raw = "Same text paragraph.\n\nSame text paragraph.\n\nUnique text paragraph."
    docs, corpus_hash = indexer._build_documents(raw, source_file="backend/data/Solutions.txt")

    assert len(docs) == 2
    assert docs[0]["doc_id"].startswith("sol_")
    assert docs[0]["doc_id"] != docs[1]["doc_id"]
    assert corpus_hash


def test_vectorrag_indexer_assigns_topic_only_display_name():
    indexer = VectorRAGIndexer()
    topic = indexer._extract_topic(
        "SSL wildcard certificate expiry incidents can be mitigated by implementing lifecycle management.",
        fallback_index=1,
    )
    assert "mitigated" not in topic.lower()
    assert "ssl wildcard certificate expiry incidents" in topic.lower()


def test_vectorrag_service_returns_disabled_message_when_feature_is_off():
    original_enabled = settings.vectorrag_enabled
    try:
        settings.vectorrag_enabled = False
        result = asyncio.run(vectorrag_v2_service.query("How to handle SSL certificate expiry?"))
    finally:
        settings.vectorrag_enabled = original_enabled

    assert "disabled" in str(result.get("answer", "")).lower()
