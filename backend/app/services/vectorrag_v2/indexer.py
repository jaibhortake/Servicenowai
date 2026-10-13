from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.config import settings
from app.services.ai_service import get_client
from app.services.neo4j_query_runtime import platform_neo4j_query_runtime_service as neo4j_query_runtime_service

from .types import VectorRAGIndexResult

logger = logging.getLogger(__name__)

_VECTORRAG_INDEX_NAME = "solution_document_embedding_idx"
_VECTORRAG_LABEL = "SolutionDocument"
_EMBEDDING_DIMENSIONS = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class VectorRAGIndexer:
    def __init__(self) -> None:
        self._openai_client = None

    def ensure_index(self) -> bool:
        return neo4j_query_runtime_service.ensure_vectorrag_vector_index(
            index_name=_VECTORRAG_INDEX_NAME,
            label=_VECTORRAG_LABEL,
            dimensions=self._embedding_dimensions(
                settings.openai_embedding_dimension_model(settings.vectorrag_embedding_model)
            ),
            property_name="embedding_vector",
            similarity_function="cosine",
        )

    def reindex(self) -> VectorRAGIndexResult:
        source_path = self._resolve_source_path(settings.vectorrag_solutions_path)
        source_file = str(source_path).replace("\\", "/")
        if not settings.vectorrag_enabled:
            return VectorRAGIndexResult(ok=False, reason="vectorrag_enabled is false", source_file=source_file)
        if not settings.openai_embedding_configured:
            return VectorRAGIndexResult(ok=False, reason="OpenAI embedding provider is not configured", source_file=source_file)
        if not neo4j_query_runtime_service.is_available():
            return VectorRAGIndexResult(ok=False, reason="neo4j query runtime unavailable", source_file=source_file)
        if not source_path.exists():
            return VectorRAGIndexResult(ok=False, reason=f"Solutions file not found: {source_file}", source_file=source_file)

        if not self.ensure_index():
            return VectorRAGIndexResult(ok=False, reason="failed to ensure vector index", source_file=source_file)

        raw_text = source_path.read_text(encoding="utf-8", errors="ignore")
        documents, corpus_hash = self._build_documents(raw_text, source_file=source_file)
        before_count = self._source_doc_count(source_file)
        if not documents:
            neo4j_query_runtime_service.delete_vectorrag_solution_documents_not_in_ids(source_file=source_file, doc_ids=[])
            neo4j_query_runtime_service.upsert_vectorrag_corpus_metadata(
                source_file=source_file,
                corpus_hash=corpus_hash,
                doc_count=0,
                embedding_model=settings.openai_embedding_model(settings.vectorrag_embedding_model),
            )
            return VectorRAGIndexResult(
                ok=True,
                indexed_count=0,
                deleted_count=before_count,
                source_file=source_file,
                corpus_hash=corpus_hash,
                embedding_model=settings.openai_embedding_model(settings.vectorrag_embedding_model),
            )

        contents = [doc["content"] for doc in documents]
        vectors = self._embed_batch(contents, settings.openai_embedding_model(settings.vectorrag_embedding_model))
        if len(vectors) != len(documents):
            return VectorRAGIndexResult(ok=False, reason="embedding count mismatch", source_file=source_file)

        indexed_count = 0
        for doc, vector in zip(documents, vectors):
            payload = {
                **doc,
                "embedding_vector": vector,
                "embedding_model": settings.openai_embedding_model(settings.vectorrag_embedding_model),
            }
            if neo4j_query_runtime_service.upsert_vectorrag_solution_document(payload):
                indexed_count += 1

        doc_ids = [doc["doc_id"] for doc in documents]
        delete_ok = neo4j_query_runtime_service.delete_vectorrag_solution_documents_not_in_ids(
            source_file=source_file,
            doc_ids=doc_ids,
        )
        metadata_ok = neo4j_query_runtime_service.upsert_vectorrag_corpus_metadata(
            source_file=source_file,
            corpus_hash=corpus_hash,
            doc_count=len(documents),
            embedding_model=settings.openai_embedding_model(settings.vectorrag_embedding_model),
        )

        if not delete_ok or not metadata_ok:
            return VectorRAGIndexResult(
                ok=False,
                reason="indexing completed but metadata sync failed",
                indexed_count=indexed_count,
                source_file=source_file,
                corpus_hash=corpus_hash,
                embedding_model=settings.openai_embedding_model(settings.vectorrag_embedding_model),
            )

        after_count = self._source_doc_count(source_file)
        deleted_count = max(0, before_count - after_count)
        return VectorRAGIndexResult(
            ok=True,
            indexed_count=indexed_count,
            deleted_count=deleted_count,
            source_file=source_file,
            corpus_hash=corpus_hash,
            embedding_model=settings.openai_embedding_model(settings.vectorrag_embedding_model),
        )

    def _build_documents(self, raw_text: str, source_file: str) -> Tuple[List[Dict[str, Any]], str]:
        paragraphs = self._split_paragraphs(raw_text)
        seen_ids = set()
        documents: List[Dict[str, Any]] = []
        ordered_hash_input: List[str] = []

        for index, paragraph in enumerate(paragraphs, start=1):
            normalized = self._normalize_text(paragraph)
            if not normalized:
                continue
            doc_id = self._stable_doc_id(normalized)
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            topic = self._extract_topic(normalized, fallback_index=index)
            documents.append(
                {
                    "doc_id": doc_id,
                    "name": topic,
                    "display_name": topic,
                    "content": normalized,
                    "source_file": source_file,
                    "source_index": len(documents) + 1,
                    "content_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                }
            )
            ordered_hash_input.append(normalized)

        corpus_hash = hashlib.sha256("\n\n".join(ordered_hash_input).encode("utf-8")).hexdigest()
        return documents, corpus_hash

    def _split_paragraphs(self, text: str) -> List[str]:
        normalized = str(text or "").replace("\r\n", "\n")
        chunks = re.split(r"\n\s*\n+", normalized)
        return [chunk.strip() for chunk in chunks if chunk and chunk.strip()]

    def _normalize_text(self, text: str) -> str:
        flattened = re.sub(r"\s+", " ", str(text or "")).strip()
        return flattened

    def _stable_doc_id(self, normalized_text: str) -> str:
        digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        return f"sol_{digest[:16]}"

    def _extract_topic(self, text: str, fallback_index: int) -> str:
        base = text.split(".", 1)[0].strip(" -:")
        if not base:
            return f"Solution {fallback_index}"

        match = re.split(
            r"\b(?:should|can|could|must|will|needs to|need to|is|are)\b",
            base,
            maxsplit=1,
            flags=re.IGNORECASE,
        )
        topic = (match[0] or "").strip(" -:,")
        if not topic:
            topic = base
        topic = re.sub(r"\s+", " ", topic).strip()
        if len(topic) > 84:
            topic = topic[:84].rstrip(" ,.-")
        if not topic:
            return f"Solution {fallback_index}"
        return topic

    def _embed_batch(self, texts: List[str], model: str) -> List[List[float]]:
        batch_size = max(1, int(getattr(settings, "graphrag_embedding_batch_size", 24) or 24))
        vectors: List[List[float]] = []
        for start in range(0, len(texts), batch_size):
            chunk = texts[start:start + batch_size]
            response = self._openai().embeddings.create(model=model, input=chunk)
            vectors.extend([[float(value) for value in row.embedding] for row in response.data])
        return vectors

    def _openai(self):
        return get_client()

    def _embedding_dimensions(self, model: str) -> int:
        return int(_EMBEDDING_DIMENSIONS.get(str(model or "").strip().lower(), int(settings.embedding_dim)))

    def _resolve_source_path(self, configured_path: str) -> Path:
        raw = Path(str(configured_path or "").strip())
        if raw.is_absolute():
            return raw
        project_root = Path(__file__).resolve().parents[4]
        return (project_root / raw).resolve()

    def _source_doc_count(self, source_file: str) -> int:
        result = neo4j_query_runtime_service.execute_readonly_query(
            "MATCH (doc:SolutionDocument) WHERE doc.source_file = $source_file RETURN count(doc) AS count",
            {"source_file": source_file, "limit": 1},
            1,
            timeout_seconds=6.0,
        )
        if not result.get("ok") or not result.get("rows"):
            return 0
        return int(result["rows"][0].get("count") or 0)

