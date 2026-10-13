from __future__ import annotations

import logging
from typing import List

from app.config import settings
from app.services.ai_service import get_client
from app.services.neo4j_query_runtime import platform_neo4j_query_runtime_service as neo4j_query_runtime_service

from .types import VectorRAGSolutionDocument

logger = logging.getLogger(__name__)

_VECTORRAG_INDEX_NAME = "solution_document_embedding_idx"


class VectorRAGRetriever:
    def __init__(self) -> None:
        self._openai_client = None

    def retrieve(self, query: str, top_k: int | None = None, threshold: float | None = None) -> List[VectorRAGSolutionDocument]:
        query_text = str(query or "").strip()
        if not query_text:
            return []
        if not settings.openai_embedding_configured:
            return []

        embedding = self._embed_query(query_text)
        vector_result = neo4j_query_runtime_service.execute_vectorrag_solution_lookup(
            index_name=_VECTORRAG_INDEX_NAME,
            embedding=embedding,
            top_k=max(1, int(top_k or settings.vectorrag_top_k or 5)),
        )
        if not vector_result.get("ok"):
            logger.warning("[VECTORRAG_V2] Vector lookup failed: %s", vector_result.get("error"))
            return []

        min_score = float(threshold if threshold is not None else settings.vectorrag_similarity_threshold)
        docs: List[VectorRAGSolutionDocument] = []
        for row in list(vector_result.get("rows", []) or []):
            score = float(row.get("score") or 0.0)
            if score < min_score:
                continue
            docs.append(
                VectorRAGSolutionDocument(
                    doc_id=str(row.get("doc_id") or ""),
                    name=str(row.get("name") or ""),
                    display_name=str(row.get("display_name") or ""),
                    content=str(row.get("content") or ""),
                    source_file=str(row.get("source_file") or ""),
                    source_index=int(row.get("source_index") or 0),
                    score=score,
                )
            )

        docs.sort(key=lambda item: item.score, reverse=True)
        return docs

    def _embed_query(self, text: str) -> List[float]:
        response = self._openai().embeddings.create(
            model=settings.openai_embedding_model(settings.vectorrag_embedding_model),
            input=[text],
        )
        return [float(v) for v in response.data[0].embedding]

    def _openai(self):
        return get_client()
