from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings
from app.services.neo4j_query_runtime import platform_neo4j_query_runtime_service as neo4j_query_runtime_service

from .indexer import VectorRAGIndexer
from .retriever import VectorRAGRetriever
from .synthesizer import VectorRAGSynthesizer
from .types import VectorRAGQueryResult, VectorRAGStatus

logger = logging.getLogger(__name__)

_VECTORRAG_INDEX_NAME = "solution_document_embedding_idx"


class VectorRAGv2Service:
    def __init__(self) -> None:
        self.indexer = VectorRAGIndexer()
        self.retriever = VectorRAGRetriever()
        self.synthesizer = VectorRAGSynthesizer()

    def startup(self) -> Dict[str, Any]:
        if not settings.vectorrag_enabled:
            return {"enabled": False, "ready": False}
        if not neo4j_query_runtime_service.is_available():
            return {"enabled": True, "ready": False, "reason": "neo4j query runtime unavailable"}

        ready = self.indexer.ensure_index()
        if ready and settings.vectorrag_startup_reindex:
            try:
                result = self.indexer.reindex()
                return {"enabled": True, "ready": bool(result.ok), "reindexed": True}
            except Exception:
                logger.exception("[VECTORRAG_V2] Startup reindex failed")
                return {"enabled": True, "ready": ready, "reindexed": False}
        return {"enabled": True, "ready": ready, "reindexed": False}

    def reindex(self) -> Dict[str, Any]:
        result = self.indexer.reindex()
        return result.model_dump()

    def status(self) -> Dict[str, Any]:
        source_file = self._resolved_source_path()
        status = VectorRAGStatus(
            enabled=bool(settings.vectorrag_enabled),
            ready=False,
            source_file=source_file,
            embedding_model=settings.openai_embedding_model(settings.vectorrag_embedding_model),
            top_k=max(1, int(settings.vectorrag_top_k or 5)),
            similarity_threshold=float(settings.vectorrag_similarity_threshold),
        )
        if not settings.vectorrag_enabled:
            return status.model_dump()
        if not neo4j_query_runtime_service.is_available():
            status.status_error = "neo4j query runtime unavailable"
            return status.model_dump()

        meta = neo4j_query_runtime_service.read_vectorrag_corpus_metadata(source_file=source_file) or {}
        status.corpus_hash = str(meta.get("corpus_hash") or "")
        status.doc_count = int(meta.get("doc_count") or 0)
        status.last_indexed_at = str(meta.get("last_indexed_at") or "")

        index_result = neo4j_query_runtime_service.execute_readonly_query(
            (
                "SHOW INDEXES YIELD name, type, state "
                "WHERE name = $index_name "
                "RETURN name, type, state "
                "LIMIT 1"
            ),
            {"index_name": _VECTORRAG_INDEX_NAME, "limit": 1},
            1,
            timeout_seconds=6.0,
        )
        if index_result.get("ok") and index_result.get("rows"):
            row = index_result["rows"][0]
            status.index_state = str(row.get("state") or "")
            status.ready = status.index_state.lower() == "online"
        else:
            status.status_error = str(index_result.get("error") or "vector index status unavailable")
        return status.model_dump()

    async def query(self, question: str, user_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        _ = user_context
        if not settings.vectorrag_enabled:
            result = VectorRAGQueryResult(
                answer="VectorRAG is currently disabled by configuration.",
                sources=[],
                fallback_reason="vectorrag_disabled",
            )
            return result.model_dump()

        docs = self.retriever.retrieve(
            question,
            top_k=max(1, int(settings.vectorrag_top_k or 5)),
            threshold=float(settings.vectorrag_similarity_threshold),
        )
        answer = await self.synthesizer.answer_from_documents(question, docs)
        result = VectorRAGQueryResult(
            answer=answer,
            sources=docs[:8],
            fallback_reason="" if docs else "no_similar_solution",
        )
        return result.model_dump()

    def _resolved_source_path(self) -> str:
        raw = Path(str(settings.vectorrag_solutions_path or "").strip())
        if raw.is_absolute():
            return str(raw).replace("\\", "/")
        project_root = Path(__file__).resolve().parents[4]
        return str((project_root / raw).resolve()).replace("\\", "/")


vectorrag_v2_service = VectorRAGv2Service()
